"""Test harness: builds the app for one brand (env BRAND, default acme) in a temp dir and imports it with Databricks faked out.
Run: BRAND=acme python -m pytest tests -q   (tests/run_all.sh runs every brand)."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import local_build  # noqa: E402

BRAND = os.environ.get("BRAND", "acme")
if os.environ.get("ACCELERATOR_BUILD_DIR"):  # installer preflight: test the exact build that is about to be deployed
    BUILD = Path(os.environ["ACCELERATOR_BUILD_DIR"])
else:
    BUILD = Path(tempfile.mkdtemp(prefix=f"accel-test-{BRAND}-")) / "app"
    local_build.build(BRAND, BUILD)
os.chdir(BUILD)
sys.path.insert(0, str(BUILD))
os.environ.setdefault("DATABRICKS_HOST", "https://example.invalid")
os.environ.pop("MLFLOW_EXPERIMENT_ID", None)   # tracing becomes a no-op
os.environ.pop("DATABRICKS_APP_NAME", None)


@pytest.fixture(scope="session")
def build_dir():
    return BUILD


@pytest.fixture()
def appmod(monkeypatch):
    """The FastAPI module with Lakebase, the Files API and the agent replaced by in-memory fakes."""
    import app as appmod
    import store

    db = {"convs": {}, "files": {}, "turns": {}}
    monkeypatch.setattr(store, "init", lambda: None)
    monkeypatch.setattr(store, "get_conversation", lambda cid, user: (dict(db["convs"][cid], turns=[]) if cid in db["convs"] and db["convs"][cid]["user"] == user else None))
    monkeypatch.setattr(store, "rename", lambda *a: None)
    monkeypatch.setattr(store, "create_conversation", lambda user, dept=None: "c-new")
    monkeypatch.setattr(store, "list_conversations", lambda user: [])
    monkeypatch.setattr(store, "ws", lambda: (_ for _ in ()).throw(AssertionError("tests must not reach Databricks")))
    monkeypatch.setattr(store, "start_turn", lambda cid, text, atts, tid=None: db["turns"].setdefault(tid, {"cid": cid, "atts": atts}) and tid)
    monkeypatch.setattr(store, "finish_turn", lambda *a, **k: None)
    monkeypatch.setattr(store, "add_file", lambda *a: None)
    monkeypatch.setattr(store, "file_owner", lambda name, user: db["files"].get((name, user)))
    calls = []

    class R:
        def __init__(self, status=200, content=b"x"):
            self.status_code, self.content, self.text = status, content, ""
            self.ok = status < 400

        def json(self):
            return {"contents": []}

    def files_api(method, path, **kw):
        calls.append((method, path))
        return R(201 if method == "PUT" else 200, b"<html>page</html>")

    monkeypatch.setattr(appmod, "files_api", files_api)
    monkeypatch.setattr(appmod, "sp_token", lambda: "sp-token")
    appmod._real_library_tree = getattr(appmod, "_real_library_tree", appmod.library_tree)
    monkeypatch.setattr(appmod, "library_tree", lambda: [])
    appmod._calls, appmod._db = calls, db
    appmod.LIVE = type(appmod.LIVE)()
    return appmod


@pytest.fixture()
def client(appmod):
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as c:
        yield c


def hdr(user="alice@acme.com", **extra):
    """Headers of a same-origin browser request as forwarded by the Databricks Apps proxy."""
    return {"x-forwarded-email": user, "sec-fetch-site": "same-origin", **extra}
