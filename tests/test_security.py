"""Path confinement, file names, per-user areas and the in-flight turn registry."""
import pytest

import config
import security
from security import Busy, Forbidden


@pytest.mark.parametrize("path", [
    f"{config.LIBRARY}/../uploads/bob/secret.pdf",
    f"{config.LIBRARY}/a/../../generated/x.docx",
    f"{config.LIBRARY}/x.pdf?y=1",
    f"{config.LIBRARY}/x.pdf#frag",
    f"{config.LIBRARY}/%2e%2e/x.pdf",
    "relative/path.pdf",
    f"{config.LIBRARY}",
    None,
])
def test_confine_rejects_escape_attempts(path):
    with pytest.raises(Forbidden):
        security.confine(path, config.LIBRARY)


def test_confine_accepts_files_under_root():
    p = f"{config.LIBRARY}/Qualite/CR.docx"
    assert security.confine(p, config.LIBRARY) == p


def test_user_folders_do_not_collide_across_domains():
    assert security.user_folder("jean@a.com") != security.user_folder("jean@b.com")
    assert security.user_folder("Jean@A.com") == security.user_folder("jean@a.com")


@pytest.mark.parametrize("name,expected", [("../../etc/x.docx", "x.docx"), ("C:\\temp\\rapport final.pdf", "rapport final.pdf"),
                                           ("Capacités – §4.pptx", "Capacites 4.pptx")])
def test_safe_filename_keeps_basename_ascii(name, expected):
    assert security.safe_filename(name) == expected


@pytest.mark.parametrize("name", ["script.html", "run.sh", "noext", ""])
def test_safe_filename_rejects_unexpected_types(name):
    with pytest.raises(Forbidden):
        security.safe_filename(name)


def test_turns_are_owner_scoped_capped_and_evicted(monkeypatch):
    monkeypatch.setattr(security, "MAX_RUNNING_TURNS", 2)
    t = security.Turns()
    t.start("t1", "alice", {"status": "running"}, conversation_id="c1")
    assert t.get("t1", "bob") is None and t.get("t1", "alice") is not None
    with pytest.raises(Busy):  # second request in the same conversation
        t.start("t2", "alice", {"status": "running"}, conversation_id="c1")
    t.start("t3", "alice", {"status": "running"}, conversation_id="c2")
    with pytest.raises(Forbidden):  # per-user concurrency cap (2)
        t.start("t4", "alice", {"status": "running"}, conversation_id="c3")
    t.get("t1", "alice")["status"] = "done"
    t.finished("t1")
    monkeypatch.setattr(security.time, "time", lambda: 10 ** 12)
    t.start("t5", "bob", {"status": "running"})
    assert t.get("t1", "alice") is None  # evicted after TTL


def test_redact_keeps_prefix_size_and_hash():
    out = security.redact("x" * 5000, keep=100)
    assert out.startswith("x" * 100) and "5000 caractères" in out and "sha256:" in out
