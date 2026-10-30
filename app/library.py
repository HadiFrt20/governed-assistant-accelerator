"""Document libraries on Unity Catalog volumes, read natively.

- Each library is a UC volume. With OBO, listing, reading and adding documents use the signed-in user's own token on the Files API,
  so Unity Catalog decides who sees which library (READ VOLUME) and who may add to it (WRITE VOLUME). The app adds no rights.
- Documents are parsed by ai_parse_document (Databricks AI Functions: text, tables, figures, page numbers) on the app's SQL
  warehouse, with the built-in readers as fallback, and cached in Lakebase by path + modification time.
- Library pages are indexed in Lakebase (Postgres full text, BM25 when Lakebase Search is installed) for search_library, which
  only returns passages from the libraries the user can read.
Personal files (uploads, generated deliverables) are not here: UC grants are volume-wide, so they stay mediated by the app identity
and confined per user (security.user_folder).
"""
import hashlib
import json
import os
import threading
import time
import traceback

import requests

import config
import docs
import security
import store

HOST = "https://" + os.environ.get("DATABRICKS_HOST", "").removeprefix("https://")
LIBS = config.LIBRARIES                          # [{"id", "label", "root"}]
AI_FORMATS = {"pdf", "docx", "pptx", "doc", "ppt", "png", "jpg", "jpeg"}  # verified on Databricks: docx, pptx; the rest per the AI Functions docs
PARSER = (config.S.get("documents") or {}).get("parser", "auto")  # auto | builtin
MAX_FILES_PER_LIBRARY = 5000
_cache, _lock = {}, threading.Lock()


class LibraryError(security.Forbidden):
    pass


def _get(url, token, **kw):
    return requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=120, **kw)


def library_of(path):
    return next((l for l in LIBS if path == l["root"] or path.startswith(l["root"] + "/")), None)


def _key(token):
    return hashlib.sha256((token or "").encode()).hexdigest()[:16]


def _cached(name, token, ttl, fn):
    k = (name, _key(token))
    with _lock:
        hit = _cache.get(k)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[k] = (time.time(), val)
        if len(_cache) > 2000:
            for old in sorted(_cache, key=lambda x: _cache[x][0])[:500]:
                _cache.pop(old, None)
    return val


def forget(token=None):
    """Drop cached listings (after an upload), for one user or everyone."""
    with _lock:
        for k in [k for k in _cache if token is None or k[1] == _key(token)]:
            _cache.pop(k, None)


def _walk(root, token, limit=MAX_FILES_PER_LIBRARY):
    """Every file under root, with the Files API (paginated). Raises LibraryError(403) when UC refuses."""
    out, stack = [], [root]
    while stack and len(out) < limit:
        path, page = stack.pop(), None
        while True:
            r = _get(f"{HOST}/api/2.0/fs/directories{path}", token, params={"page_token": page} if page else None)
            if r.status_code in (401, 403):
                raise LibraryError("accès refusé par Unity Catalog")
            if r.status_code == 404:
                break
            r.raise_for_status()
            body = r.json()
            for e in body.get("contents", []):
                if e.get("is_directory"):
                    stack.append(e["path"].rstrip("/"))
                else:
                    out.append(e)
            page = body.get("next_page_token")
            if not page:
                break
    return out


def readable(token):
    """The libraries this identity may read, as decided by Unity Catalog (cached 5 min)."""
    def probe():
        ok = []
        for lib in LIBS:
            r = _get(f"{HOST}/api/2.0/fs/directories{lib['root']}", token)
            if r.ok or r.status_code == 404:  # 404: empty new volume, readable
                ok.append(lib)
            elif r.status_code not in (401, 403):
                print("LIBRARY_PROBE", lib["root"], r.status_code, r.text[:160], flush=True)
        return ok
    return _cached("readable", token, 300, probe)


def tree(token):
    """Documents of every library the identity can read (cached 2 min per identity). A library that fails is skipped, not fatal."""
    def build():
        items = []
        for lib in readable(token):
            try:
                for e in _walk(lib["root"], token):
                    rel = e["path"].removeprefix(lib["root"] + "/")
                    items.append({"path": e["path"], "name": e["name"], "library": lib["id"], "library_label": lib["label"],
                                  "folder": rel.rsplit("/", 1)[0] if "/" in rel else "", "size": e.get("file_size"), "modified": e.get("last_modified")})
            except Exception as ex:  # keep the other libraries
                print("LIBRARY_ERROR", lib["root"], ex, flush=True)
        return items
    return _cached("tree", token, 120, build)


def fetch(path, token):
    """Bytes of a file with the given identity; a refusal is reported as such (never retried with broader rights)."""
    r = _get(f"{HOST}/api/2.0/fs/files{path}", token)
    if r.status_code in (401, 403):
        raise LibraryError("accès refusé par Unity Catalog à ce document")
    if r.status_code == 404:
        raise LibraryError("document introuvable")
    r.raise_for_status()
    return r.content, r.headers.get("last-modified", "")


def put(path, data, token):
    r = requests.put(f"{HOST}/api/2.0/fs/files{path}", params={"overwrite": "false"}, data=data,
                     headers={"Authorization": f"Bearer {token}"}, timeout=300)
    if r.status_code == 409:
        raise LibraryError("un document porte déjà ce nom")
    if r.status_code in (401, 403):
        raise LibraryError("Unity Catalog refuse l'ajout : seuls les responsables de cette bibliothèque peuvent y déposer des documents")
    r.raise_for_status()
    forget()


# ---------------------------------------------------------------- parsing
def _sql(statement, params):
    from databricks.sdk.service.sql import StatementParameterListItem, StatementState
    w = store.ws()
    r = w.statement_execution.execute_statement(statement=statement, warehouse_id=config.WAREHOUSE_ID, wait_timeout="50s",
                                                parameters=[StatementParameterListItem(name=k, value=v) for k, v in params.items()])
    deadline = time.time() + 240
    while r.status.state in (StatementState.PENDING, StatementState.RUNNING) and time.time() < deadline:
        time.sleep(2)
        r = w.statement_execution.get_statement(r.statement_id)
    if r.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"ai_parse_document: {r.status.state} {(r.status.error.message if r.status.error else '')[:300]}")
    return r.result.data_array or [] if r.result else []


def render_parsed(doc):
    """ai_parse_document v2 output → text with page markers ([Page n]); tables kept, figures by their description."""
    if doc.get("error_status"):
        raise RuntimeError(f"ai_parse_document error: {doc['error_status']}")
    pages = {}
    for e in (doc.get("document") or {}).get("elements", []):
        if e.get("type") in ("page_header", "page_footer"):
            continue
        page = ((e.get("bbox") or [{}])[0].get("page_id") or 0) + 1
        text = (e.get("content") or "").strip()
        if e.get("type") == "figure":
            text = f"[Figure] {(e.get('description') or text).strip()}" if (e.get("description") or text) else ""
        if text:
            pages.setdefault(page, []).append(text)
    return "\n\n".join(f"[Page {p}]\n" + "\n".join(parts) for p, parts in sorted(pages.items()))


def parse(path, data):
    """(text, parser). ai_parse_document for office documents and PDFs, built-in readers for the rest or as fallback."""
    ext = path.rsplit(".", 1)[-1].lower()
    if PARSER != "builtin" and ext in AI_FORMATS and config.WAREHOUSE_ID:
        try:
            rows = _sql("SELECT ai_parse_document(content, map('version', '2.0')) FROM read_files(:p, format => 'binaryFile')", {"p": path})
            if rows and rows[0][0]:
                text = render_parsed(json.loads(rows[0][0]))
                if text.strip():
                    return text, "ai_parse_document"
        except Exception as e:  # unavailable function, region, format, warehouse: the built-in readers still work
            print("AI_PARSE_FALLBACK", path.rsplit("/", 1)[-1], str(e)[:200], flush=True)
    return docs.extract(path.rsplit("/", 1)[-1], data), "builtin"


def read(path, token, modified=""):
    """Text of a document read with `token`, through the Lakebase cache (path + modification time). Returns (text, meta)."""
    data, lm = fetch(path, token)  # always fetched with the caller's identity: the access check is Unity Catalog's
    modified = modified or lm
    digest = hashlib.sha256(data).hexdigest()
    try:
        hit = store.q("SELECT text, parser FROM assistant.doc_cache WHERE path=%s AND sha256=%s", (path, digest), one=True)
    except Exception:
        hit = None
    if hit:
        return hit["text"], {"parser": hit["parser"], "cached": True}
    text, parser = parse(path, data)
    try:
        store.q("""INSERT INTO assistant.doc_cache(path, sha256, modified, parser, text) VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (path) DO UPDATE SET sha256=excluded.sha256, modified=excluded.modified, parser=excluded.parser,
                   text=excluded.text, parsed_at=now()""", (path, digest, modified, parser, text or ""))
    except Exception:
        traceback.print_exc()
    return text, {"parser": parser, "cached": False}


# ---------------------------------------------------------------- index + search
def chunks(text, size=1800):
    """(page, passage) pairs: one or more passages per [Page n] block."""
    out, page = [], 1
    for block in (text or "").split("[Page "):
        if not block.strip():
            continue
        head, _, body = block.partition("]\n") if block[:1].isdigit() else ("", "", block)
        page = int(head) if head.isdigit() else page
        body = body.strip()
        for i in range(0, len(body), size):
            out.append((page, body[i:i + size]))
    return out


def index_once(sp_token):
    """Bring the search index in line with the libraries (new, changed, removed documents). Runs with the app identity,
    which has READ VOLUME on every library; search results are filtered per user afterwards."""
    stats = {"indexed": 0, "removed": 0, "errors": 0}
    seen = set()
    for lib in LIBS:
        try:
            files = _walk(lib["root"], sp_token)
        except Exception as e:
            print("INDEX_LIST", lib["root"], e, flush=True)
            continue
        known = {r["path"]: r["modified"] for r in store.q("SELECT DISTINCT path, modified FROM assistant.library_chunks WHERE library=%s", (lib["id"],))}
        for f in files:
            seen.add(f["path"])
            mod = str(f.get("last_modified") or "")
            if known.get(f["path"]) == mod:
                continue
            try:
                text, _ = read(f["path"], sp_token, mod)
                rows = [(f["path"], lib["id"], p, c, mod) for p, c in chunks(text)]
                store.q("DELETE FROM assistant.library_chunks WHERE path=%s", (f["path"],))
                for row in rows:
                    store.q("INSERT INTO assistant.library_chunks(path, library, page, content, modified) VALUES (%s,%s,%s,%s,%s)", row)
                stats["indexed"] += 1
            except Exception as e:
                stats["errors"] += 1
                print("INDEX_FILE", f["path"], str(e)[:200], flush=True)
        for gone in set(known) - seen:
            store.q("DELETE FROM assistant.library_chunks WHERE path=%s", (gone,))
            stats["removed"] += 1
    return stats


def start_indexer(sp_token_fn, every=600):
    """Background indexing: at start-up, every `every` seconds, and right after an upload (wake())."""
    def loop():
        while True:
            try:
                s = index_once(sp_token_fn())
                if s["indexed"] or s["removed"] or s["errors"]:
                    print("LIBRARY_INDEX", s, flush=True)
            except Exception:
                traceback.print_exc()
            _wake.wait(every)
            _wake.clear()
    threading.Thread(target=loop, daemon=True, name="library-indexer").start()


_wake = threading.Event()


def wake():
    _wake.set()


def search(query, token, limit=8):
    """Best passages for `query` in the libraries the identity can read: [{path, name, library, page, snippet}]."""
    libs = [l["id"] for l in readable(token)]
    if not libs or not query.strip():
        return []
    # any significant word may match (a question is not a phrase); ranking favours passages holding more of them
    anyq = "replace(plainto_tsquery('french', %s)::text, '&', '|')::tsquery"
    rows = store.q(f"""SELECT path, library, page, ts_headline('french', content, {anyq},
                              'MaxWords=40,MinWords=15,MaxFragments=2,StartSel=«,StopSel=»') AS snippet,
                              ts_rank_cd(tsv, {anyq}) AS score
                       FROM assistant.library_chunks
                       WHERE library = ANY(%s) AND tsv @@ {anyq}
                       ORDER BY score DESC LIMIT %s""", (query, query, libs, query, limit))
    return [{"path": r["path"], "name": r["path"].rsplit("/", 1)[-1], "library": r["library"], "page": r["page"], "snippet": r["snippet"]} for r in rows]
