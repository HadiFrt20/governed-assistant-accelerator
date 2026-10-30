"""Libraries on UC volumes: native access checks (the user's token), ai_parse_document rendering, cache, index and search (no network)."""
import json
from types import SimpleNamespace

import pytest

import agent
import config
import library
import security


class R:
    def __init__(self, status=200, body=None, content=b"", headers=None):
        self.status_code, self._body, self.content, self.headers = status, body or {}, content, headers or {}
        self.ok, self.text = status < 400, json.dumps(body or {})

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def fresh_cache():
    library.forget()
    yield
    library.forget()


def test_listing_follows_pagination_and_subfolders(monkeypatch):
    root = config.LIBRARY

    def get(url, headers=None, params=None, timeout=None):
        assert headers["Authorization"] == "Bearer user-tok"   # always the caller's identity
        if url.endswith(f"directories{root}") and not params:
            return R(body={"contents": [{"path": f"{root}/a.pdf", "name": "a.pdf"}, {"path": f"{root}/Q/", "name": "Q", "is_directory": True}],
                           "next_page_token": "p2"})
        if url.endswith(f"directories{root}") and params == {"page_token": "p2"}:
            return R(body={"contents": [{"path": f"{root}/b.pdf", "name": "b.pdf"}]})
        if url.endswith(f"directories{root}/Q"):
            return R(body={"contents": [{"path": f"{root}/Q/c.docx", "name": "c.docx"}]})
        return R(404)
    monkeypatch.setattr(library.requests, "get", get)
    items = library.tree("user-tok")
    assert sorted(i["name"] for i in items) == ["a.pdf", "b.pdf", "c.docx"]
    assert next(i for i in items if i["name"] == "c.docx")["folder"] == "Q"


def test_unity_catalog_refusal_hides_the_library(monkeypatch):
    monkeypatch.setattr(library.requests, "get", lambda url, **k: R(403))
    assert library.readable("no-rights") == [] and library.tree("no-rights") == []
    with pytest.raises(security.Forbidden, match="Unity Catalog"):
        library.fetch(f"{config.LIBRARY}/a.pdf", "no-rights")


def test_listings_are_cached_per_identity(monkeypatch):
    calls = []
    monkeypatch.setattr(library.requests, "get", lambda url, **k: calls.append(k["headers"]["Authorization"]) or R(body={"contents": []}))
    library.tree("alice")
    library.tree("alice")
    n = len(calls)
    library.tree("bob")  # another user: never served from alice's cache
    assert len(calls) > n and "Bearer bob" in calls


def test_parsed_document_keeps_pages_tables_and_figures_without_headers():
    doc = {"document": {"elements": [
        {"type": "page_header", "content": "En-tête", "bbox": [{"page_id": 0}]},
        {"type": "title", "content": "Procédure", "bbox": [{"page_id": 0}]},
        {"type": "table", "content": "<table><tr><td>45 min</td></tr></table>", "bbox": [{"page_id": 1}]},
        {"type": "figure", "content": "", "description": "Graphique des volumes par site", "bbox": [{"page_id": 1}]},
        {"type": "page_footer", "content": "Page 2", "bbox": [{"page_id": 1}]}]}, "error_status": None}
    text = library.render_parsed(doc)
    assert text.startswith("[Page 1]\nProcédure") and "[Page 2]" in text and "45 min" in text
    assert "[Figure] Graphique des volumes par site" in text and "En-tête" not in text and "Page 2\n" not in text
    assert library.chunks(text) == [(1, "Procédure"), (2, "<table><tr><td>45 min</td></tr></table>\n[Figure] Graphique des volumes par site")]


def test_parse_falls_back_to_builtin_readers(monkeypatch):
    monkeypatch.setattr(library, "_sql", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("AI function unavailable in region")))
    monkeypatch.setattr(library.docs, "extract", lambda name, data: "texte lu localement")
    assert library.parse(f"{config.LIBRARY}/a.pdf", b"%PDF") == ("texte lu localement", "builtin")


def test_parse_uses_ai_parse_document_for_office_files(monkeypatch):
    seen = {}
    doc = {"document": {"elements": [{"type": "text", "content": "Délai : 37 minutes", "bbox": [{"page_id": 0}]}]}}
    monkeypatch.setattr(library, "_sql", lambda stmt, params: seen.update(stmt=stmt, **params) or [[json.dumps(doc)]])
    text, parser = library.parse(f"{config.LIBRARY}/proc.docx", b"PK")
    assert parser == "ai_parse_document" and "37 minutes" in text
    assert ":p" in seen["stmt"] and seen["p"].endswith("proc.docx")   # the path is a bound parameter, never spliced into SQL


def test_spreadsheets_skip_ai_parse(monkeypatch):
    monkeypatch.setattr(library, "_sql", lambda *a, **k: pytest.fail("xlsx is read by the built-in reader"))
    monkeypatch.setattr(library.docs, "extract", lambda name, data: "a;b")
    assert library.parse(f"{config.LIBRARY}/t.xlsx", b"PK")[1] == "builtin"


def test_read_uses_cache_and_always_checks_access_first(monkeypatch):
    fetched, stored = [], []
    monkeypatch.setattr(library, "fetch", lambda path, token: fetched.append(token) or (b"same bytes", "Mon, 01 Jan"))
    monkeypatch.setattr(library.store, "q", lambda sql, args=(), one=False: ({"text": "cached", "parser": "ai_parse_document"} if "SELECT" in sql else stored.append(sql)))
    text, meta = library.read(f"{config.LIBRARY}/a.pdf", "user-tok")
    assert (text, meta["cached"]) == ("cached", True) and fetched == ["user-tok"] and not stored


def test_search_only_covers_readable_libraries(monkeypatch):
    sent = {}
    monkeypatch.setattr(library, "readable", lambda token: [l for l in config.LIBRARIES if token == "reader"])
    monkeypatch.setattr(library.store, "q", lambda sql, args=(), one=False: sent.update(sql=sql, args=args) or
                        [{"path": f"{config.LIBRARY}/p.pdf", "library": config.LIBRARIES[0]["id"], "page": 3, "snippet": "«quai» 45 min"}])
    assert library.search("délai quai", "outsider") == [] and not sent   # no readable library: nothing queried
    hits = library.search("délai quai", "reader")
    assert hits == [{"path": f"{config.LIBRARY}/p.pdf", "name": "p.pdf", "library": config.LIBRARIES[0]["id"], "page": 3, "snippet": "«quai» 45 min"}]
    assert sent["args"][2] == [l["id"] for l in config.LIBRARIES] and "ANY(%s)" in sent["sql"]


def test_attachments_use_the_users_token_for_libraries_and_the_app_for_own_uploads(monkeypatch):
    used = []
    monkeypatch.setattr(agent.library, "read", lambda path, token: used.append((path.rsplit("/", 1)[-1], token)) or ("x" * 150_000, {"parser": "builtin", "cached": False}))
    uf = security.user_folder("alice@acme.com")
    j = {"events": [], "messages": [{"role": "user", "content": "q"}]}
    agent.read_attachments(j, "sp", [{"name": "mine.pdf", "path": f"{uf}/mine.pdf"}, {"name": "lib.pdf", "path": f"{config.LIBRARY}/lib.pdf"}],
                           "q", uf, user_token="user")
    assert used == [("mine.pdf", "sp"), ("lib.pdf", "user")]
    assert len(j["messages"][-1]["content"]) < 205_000 and "limite par demande" in j["messages"][-1]["content"]
    with pytest.raises(security.Forbidden):
        agent.read_attachments(j, "sp", [{"name": "x.pdf", "path": f"{security.user_folder('bob@acme.com')}/x.pdf"}], "q", uf, user_token="user")


def test_refused_library_document_is_reported_not_retried(monkeypatch):
    def refuse(path, token):
        raise library.LibraryError("accès refusé par Unity Catalog à ce document")
    monkeypatch.setattr(agent.library, "read", refuse)
    j = {"events": [], "messages": [{"role": "user", "content": "q"}]}
    agent.read_attachments(j, "sp", [{"name": "lib.pdf", "path": f"{config.LIBRARY}/lib.pdf"}], "q", security.user_folder("a@b.c"), user_token="user")
    assert any("Unity Catalog" in e["text"] for e in j["events"] if e["type"] == "error")


def test_search_tool_cites_pages(monkeypatch):
    from test_agent import FakeGateway, text, tool_use
    monkeypatch.setattr(agent.library, "search", lambda q, token: [{"path": "/Volumes/c/s/b/p.pdf", "name": "p.pdf", "library": "b", "page": 4, "snippet": "45 min"}])
    j = {"events": [], "files": [], "answer": None, "messages": [{"role": "user", "content": "délai de chargement ?"}]}
    gw = FakeGateway(j, [tool_use("search_library", {"query": "délai chargement"}), text("45 minutes (p.pdf, p. 4).")])
    agent.agent_loop(gw, j, "alice@acme.com", None, "", security.user_folder("alice@acme.com"))
    result = json.loads(gw.sent[1][-1]["content"][0]["content"])
    assert result["resultats"][0]["page"] == 4 and "p. N" in result["consigne"]


def test_index_reparses_only_changed_documents(monkeypatch):
    lib = config.LIBRARIES[0]
    monkeypatch.setattr(library, "LIBS", [lib])
    monkeypatch.setattr(library, "_walk", lambda root, token: [{"path": f"{root}/same.pdf", "last_modified": "1"}, {"path": f"{root}/new.pdf", "last_modified": "2"}])
    executed, parsed = [], []
    def q(sql, args=(), one=False):
        executed.append(sql.split()[0])
        if sql.startswith("SELECT DISTINCT"):
            return [{"path": f"{lib['root']}/same.pdf", "modified": "1"}, {"path": f"{lib['root']}/gone.pdf", "modified": "1"}]
        return []
    monkeypatch.setattr(library.store, "q", q)
    monkeypatch.setattr(library, "read", lambda path, token, mod="": parsed.append(path) or ("[Page 1]\nabc", {}))
    stats = library.index_once("sp")
    assert parsed == [f"{lib['root']}/new.pdf"] and stats == {"indexed": 1, "removed": 1, "errors": 0}
