"""HTTP routes: authorization, path handling, limits, headers. Lakebase, the Files API and the agent are faked (conftest)."""
import io

import config
import security
from conftest import hdr


def _conv(appmod, cid="c1", user="alice@acme.com"):
    appmod._db["convs"][cid] = {"id": cid, "user": user, "department": None, "llm_messages": []}


def test_index_sets_csp_and_ai_notice(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "intelligence artificielle" in r.text and "cdn.jsdelivr" not in r.text


def test_library_download_blocks_traversal_without_fetching(client, appmod):
    r = client.get("/api/library/download", params={"path": f"{config.LIBRARY}/../uploads/bob/secret.pdf"}, headers=hdr())
    assert r.status_code == 403 and appmod._calls == []


def test_send_rejects_attachment_outside_own_folder(client, appmod, monkeypatch):
    _conv(appmod)
    ran = []
    monkeypatch.setattr(appmod.agent, "run_agent", lambda *a, **k: ran.append(1))
    other = f"{security.user_folder('bob@acme.com')}/abc_secret.docx"
    r = client.post("/api/conversations/c1/messages", json={"text": "Résume", "attachments": [{"name": "secret.docx", "path": other}]}, headers=hdr())
    assert r.status_code == 403 and not ran


def test_send_rejects_oversized_prompt(client, appmod):
    _conv(appmod)
    r = client.post("/api/conversations/c1/messages", json={"text": "x" * (security.MAX_PROMPT_CHARS + 1)}, headers=hdr())
    assert r.status_code == 403


def test_send_refuses_second_turn_in_same_conversation(client, appmod, monkeypatch):
    _conv(appmod)
    import threading
    gate = threading.Event()
    monkeypatch.setattr(appmod.agent, "run_agent", lambda job, *a, **k: gate.wait(5))
    assert client.post("/api/conversations/c1/messages", json={"text": "un"}, headers=hdr()).status_code == 200
    assert client.post("/api/conversations/c1/messages", json={"text": "deux"}, headers=hdr()).status_code == 409
    gate.set()


def test_poll_is_owner_scoped(client, appmod, monkeypatch):
    _conv(appmod)
    monkeypatch.setattr(appmod.agent, "run_agent", lambda job, *a, **k: job.update(answer="ok"))
    tid = client.post("/api/conversations/c1/messages", json={"text": "bonjour"}, headers=hdr()).json()["turn_id"]
    assert client.get(f"/api/turns/{tid}", headers=hdr("bob@acme.com")).status_code == 404
    assert client.get(f"/api/turns/{tid}", headers=hdr()).status_code == 200


def test_upload_stays_in_user_folder_and_never_overwrites(client, appmod):
    r = client.post("/api/upload", files={"file": ("../../library/Qualite/CR.docx", io.BytesIO(b"x"), "application/octet-stream")}, headers=hdr())
    assert r.status_code == 200
    method, path = appmod._calls[-1]
    assert method == "PUT" and path.startswith(f"files{security.user_folder('alice@acme.com')}/") and "overwrite=false" in path and ".." not in path


def test_upload_rejects_executable_types(client):
    r = client.post("/api/upload", files={"file": ("page.html", io.BytesIO(b"<script>"), "text/html")}, headers=hdr())
    assert r.status_code == 403


def test_library_upload_requires_curator(client, appmod):
    r = client.post("/api/library/upload", files={"file": ("x.docx", io.BytesIO(b"x"), "application/octet-stream")}, data={"folder": "../../x"}, headers=hdr("eve@acme.com"))
    assert r.status_code == 403 and appmod._calls == []


def test_file_download_requires_ownership_and_sandboxes_html(client, appmod):
    appmod._db["files"][("p.html", "alice@acme.com")] = {"user_email": "alice@acme.com", "path": f"{config.GENERATED}/p.html", "kind": "html"}
    assert client.get("/api/files/p.html", headers=hdr("bob@acme.com")).status_code == 403
    r = client.get("/api/files/p.html", headers=hdr())
    csp = r.headers["content-security-policy"]
    assert r.status_code == 200 and "sandbox" in csp and "connect-src 'none'" in csp and "default-src 'none'" in csp


def test_cross_origin_writes_are_refused(client, appmod):
    _conv(appmod)
    r = client.post("/api/conversations", json={}, headers=hdr(origin="https://evil.example"))
    assert r.status_code == 403


def test_missing_identity_is_refused(client):
    assert client.get("/api/conversations").status_code == 401


def test_cross_site_browser_write_is_refused_but_non_browser_client_allowed(client, appmod):
    assert client.post("/api/conversations", json={}, headers={"x-forwarded-email": "a@acme.com", "sec-fetch-site": "cross-site"}).status_code == 403
    assert client.post("/api/conversations", json={}, headers={"x-forwarded-email": "a@acme.com", "sec-fetch-site": "same-site"}).status_code == 403
    assert client.post("/api/conversations", json={}, headers={"x-forwarded-email": "a@acme.com"}).status_code == 200  # script / API client


def test_oversized_json_body_is_refused(client):
    r = client.post("/api/conversations", content=b"{" + b" " * 1_100_000 + b"}", headers=hdr(**{"content-type": "application/json"}))
    assert r.status_code == 413
