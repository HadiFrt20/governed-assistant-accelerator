"""Conversation history in Lakebase Postgres (schema `assistant`, owned by the app service principal)."""
import json
import os
import threading
import time
import uuid

import psycopg
from databricks.sdk import WorkspaceClient
from psycopg.rows import dict_row

_w = None
_lock = threading.RLock()  # guards the shared connection: replacement and execution never overlap
_conn, _exp = None, 0


def ws():
    """Workspace client created on first use (importing this module must not need Databricks credentials, e.g. in tests)."""
    global _w
    if _w is None:
        _w = WorkspaceClient()
    return _w

DDL = """
CREATE SCHEMA IF NOT EXISTS assistant;
CREATE TABLE IF NOT EXISTS assistant.conversations (
  id TEXT PRIMARY KEY, user_email TEXT NOT NULL, title TEXT NOT NULL DEFAULT 'Nouvelle conversation',
  department TEXT, llm_messages JSONB NOT NULL DEFAULT '[]', created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now());
CREATE INDEX IF NOT EXISTS conv_user ON assistant.conversations(user_email, updated_at DESC);
CREATE TABLE IF NOT EXISTS assistant.turns (
  id TEXT PRIMARY KEY, conversation_id TEXT REFERENCES assistant.conversations(id) ON DELETE CASCADE,
  user_text TEXT, attachments JSONB DEFAULT '[]', answer TEXT, events JSONB DEFAULT '[]', status TEXT DEFAULT 'running',
  created_at TIMESTAMPTZ DEFAULT now());
CREATE INDEX IF NOT EXISTS turns_conv ON assistant.turns(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS assistant.files (
  id TEXT PRIMARY KEY, user_email TEXT NOT NULL, conversation_id TEXT, name TEXT, title TEXT, kind TEXT, path TEXT,
  created_at TIMESTAMPTZ DEFAULT now());
CREATE INDEX IF NOT EXISTS files_user ON assistant.files(user_email, created_at DESC);
ALTER TABLE assistant.files ADD COLUMN IF NOT EXISTS meta JSONB DEFAULT '{}';
ALTER TABLE assistant.conversations ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE assistant.turns ADD COLUMN IF NOT EXISTS doc_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('french', coalesce(user_text, '') || ' ' || coalesce(answer, ''))) STORED;
CREATE INDEX IF NOT EXISTS turns_fts ON assistant.turns USING gin (doc_tsv);
CREATE UNIQUE INDEX IF NOT EXISTS files_user_name ON assistant.files(user_email, name);
CREATE INDEX IF NOT EXISTS files_conv ON assistant.files(conversation_id);
"""
BM25 = {"on": False}


def lakebase_env():
    """LAKEBASE_ENDPOINT comes from the bundle (resources/app.yml); PGHOST and PGUSER are injected by Databricks Apps from the
    app's postgres resource. A missing value means the app was deployed without that resource: say so instead of guessing."""
    missing = [k for k in ("LAKEBASE_ENDPOINT", "PGHOST", "PGUSER") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Lakebase non configuré pour cette app ({', '.join(missing)} absent) : redéployez le bundle (install/install.py), "
                           "qui déclare la ressource postgres de l'app.")
    return {k: os.environ[k] for k in ("LAKEBASE_ENDPOINT", "PGHOST", "PGUSER")}


def conn():
    global _conn, _exp
    with _lock:
        if _conn is None or _conn.closed or time.time() > _exp:
            if _conn is not None and not _conn.closed:
                _conn.close()
            env = lakebase_env()
            tok = ws().postgres.generate_database_credential(endpoint=env["LAKEBASE_ENDPOINT"]).token
            _conn = psycopg.connect(host=env["PGHOST"], port=int(os.environ.get("PGPORT", "5432")), dbname=os.environ.get("PGDATABASE", "databricks_postgres"),
                                    user=env["PGUSER"], password=tok, sslmode="require", autocommit=True, row_factory=dict_row)
            _exp = time.time() + 40 * 60
        return _conn


def q(sql, args=(), one=False):
    global _exp
    for attempt in range(2):
        try:
            with _lock:
                c = conn()
                cur = c.execute(sql, args)
                rows = cur.fetchall() if cur.description else []
            return (rows[0] if rows else None) if one else rows
        except (psycopg.OperationalError, psycopg.InterfaceError):
            with _lock:
                _exp = 0  # force a fresh connection (and credential) on the next attempt
            if attempt:
                raise


def init():
    c = conn()
    c.execute(DDL)
    enable_bm25()
    # turns left running by a restart can never finish (their worker thread is gone): close them visibly
    q("UPDATE assistant.turns SET status='error', answer=coalesce(answer, 'Interrompu par un redémarrage du service. Relancez la demande.') "
      "WHERE status='running' AND created_at < now() - interval '2 minutes'")


def enable_bm25():
    """Use Lakebase Search (BM25) when the lakebase_text extension is installed; otherwise native Postgres full-text."""
    try:
        if q("SELECT 1 FROM pg_extension WHERE extname = 'lakebase_text'", one=True):
            q("CREATE INDEX IF NOT EXISTS turns_bm25 ON assistant.turns USING lakebase_bm25 (doc_tsv tsvector_bm25_ops)")
            BM25["on"] = True
    except Exception as e:
        print("BM25 unavailable:", e, flush=True)
    return BM25["on"]


def list_conversations(user):
    return q("SELECT id, title, department, pinned, updated_at FROM assistant.conversations WHERE user_email=%s ORDER BY pinned DESC, updated_at DESC LIMIT 100", (user,))


def set_pinned(cid, user, pinned):
    q("UPDATE assistant.conversations SET pinned=%s WHERE id=%s AND user_email=%s", (bool(pinned), cid, user))


HEADLINE = "ts_headline('french', regexp_replace(coalesce(t.answer, t.user_text), '[*#`>|]', '', 'g'), plainto_tsquery('french', %s), 'MaxWords=22,MinWords=10,MaxFragments=1,StartSel=<mark>,StopSel=</mark>')"


def search(user, text):
    """Full-text search over the user's conversations; best turn per conversation."""
    if not BM25["on"]:
        enable_bm25()
    if BM25["on"]:
        rows = q(f"""SELECT * FROM (
                 SELECT t.conversation_id, c.title, c.pinned, c.updated_at, {HEADLINE} AS snippet,
                        t.doc_tsv <@> to_bm25query(to_tsvector('french', %s), 'assistant.turns_bm25'::regclass) AS score
                 FROM assistant.turns t JOIN assistant.conversations c ON c.id = t.conversation_id
                 WHERE c.user_email = %s) s WHERE score < 0 ORDER BY score LIMIT 60""", (text, text, user))
        engine = "Lakebase Search · BM25"
    else:
        rows = q(f"""SELECT t.conversation_id, c.title, c.pinned, c.updated_at, {HEADLINE} AS snippet,
                        -ts_rank_cd(t.doc_tsv, plainto_tsquery('french', %s)) AS score
                 FROM assistant.turns t JOIN assistant.conversations c ON c.id = t.conversation_id
                 WHERE c.user_email = %s AND t.doc_tsv @@ plainto_tsquery('french', %s)
                 ORDER BY score LIMIT 60""", (text, text, user, text))
        engine = "Postgres plein texte"
    seen, out = set(), []
    for r in rows:
        if r["conversation_id"] not in seen:
            seen.add(r["conversation_id"])
            out.append(r)
    return {"engine": engine, "results": out[:20]}


def create_conversation(user, department=None):
    cid = uuid.uuid4().hex
    q("INSERT INTO assistant.conversations(id, user_email, department) VALUES (%s,%s,%s)", (cid, user, department))
    return cid


def get_conversation(cid, user):
    c = q("SELECT * FROM assistant.conversations WHERE id=%s AND user_email=%s", (cid, user), one=True)
    if not c:
        return None
    c["turns"] = q("SELECT id, user_text, attachments, answer, events, status, created_at FROM assistant.turns WHERE conversation_id=%s ORDER BY created_at", (cid,))
    c["files"] = q("SELECT id, name, title, kind, path, created_at FROM assistant.files WHERE conversation_id=%s ORDER BY created_at", (cid,))
    return c


def rename(cid, user, title):
    q("UPDATE assistant.conversations SET title=%s, updated_at=now() WHERE id=%s AND user_email=%s", (title[:120], cid, user))


def delete(cid, user):
    q("DELETE FROM assistant.conversations WHERE id=%s AND user_email=%s", (cid, user))


def start_turn(cid, text, attachments, tid=None):
    tid = tid or uuid.uuid4().hex
    q("INSERT INTO assistant.turns(id, conversation_id, user_text, attachments) VALUES (%s,%s,%s,%s)", (tid, cid, text, json.dumps(attachments)))
    q("UPDATE assistant.conversations SET updated_at=now() WHERE id=%s", (cid,))
    return tid


def fail_turn(tid, message):
    """Last resort when a turn cannot be saved: never leave it 'running'."""
    q("UPDATE assistant.turns SET status='error', answer=%s WHERE id=%s", (message, tid))


def finish_turn(tid, cid, answer, events, llm_messages, status="done"):
    q("UPDATE assistant.turns SET answer=%s, events=%s, status=%s WHERE id=%s", (answer, json.dumps(events, ensure_ascii=False), status, tid))
    q("UPDATE assistant.conversations SET llm_messages=%s, updated_at=now() WHERE id=%s", (json.dumps(llm_messages, ensure_ascii=False), cid))


def add_file(user, cid, f):
    q("INSERT INTO assistant.files(id, user_email, conversation_id, name, title, kind, path, meta) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
      (uuid.uuid4().hex, user, cid, f["name"], f.get("title"), f["kind"], f["path"],
       json.dumps({"template": f.get("template"), "outline": f.get("outline")}, ensure_ascii=False)))


def list_files(user):
    return q("SELECT f.name, f.title, f.kind, f.path, f.meta, f.created_at, f.conversation_id, c.title AS conversation FROM assistant.files f "
             "LEFT JOIN assistant.conversations c ON c.id=f.conversation_id WHERE f.user_email=%s ORDER BY f.created_at DESC LIMIT 200", (user,))


def file_owner(name, user):
    """The file record if it belongs to `user`, else None (ownership is part of the lookup, not a later comparison)."""
    return q("SELECT user_email, path, kind FROM assistant.files WHERE name=%s AND user_email=%s", (name, user), one=True)
