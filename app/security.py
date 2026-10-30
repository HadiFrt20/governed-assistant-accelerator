"""Security controls shared by the API and the agent: path confinement, per-user upload areas, file names, limits, redaction.
Every file access goes through the app service principal, so the app itself must enforce who can reach which path."""
import hashlib
import posixpath
import re
import threading
import time
import unicodedata

import config

SEC = config.S.get("security", {})
OBO_FALLBACK_TO_APP = bool(SEC.get("obo_fallback_to_app", False))   # never act as the app when the user's token is refused, unless explicitly enabled
LIBRARY_CURATORS = {e.lower() for e in SEC.get("library_curators", [])}
MAX_PROMPT_CHARS = int(SEC.get("max_prompt_chars", 20000))
MAX_UPLOAD_BYTES = int(SEC.get("max_upload_mb", 25)) * 1024 * 1024
MAX_RUNNING_TURNS = int(SEC.get("max_running_turns_per_user", 2))
TRACE_CONTENT = SEC.get("trace_content", "redacted")                # "redacted" keeps names, sizes and hashes of documents, not their text
ALLOWED_EXT = {"docx", "pptx", "pdf", "txt", "csv", "md", "xlsx"}


class Forbidden(Exception):
    pass


class Busy(Exception):
    pass


def confine(path, *roots):
    """Normalise a /Volumes path and require it to sit under one of the roots. Rejects traversal, query strings and fragments."""
    if not isinstance(path, str) or not path.startswith("/") or any(c in path for c in "?#%\\\x00"):
        raise Forbidden("chemin invalide")
    if ".." in path.split("/"):
        raise Forbidden("chemin invalide")
    norm = posixpath.normpath(path)
    for root in roots:
        root = posixpath.normpath(root)
        if norm.startswith(root + "/"):
            return norm
    raise Forbidden("chemin hors de l'espace autorisé")


def user_folder(user):
    """Per-user upload area. Uses the full e-mail (two people can share a local part across domains)."""
    u = user.lower()
    return f"{config.UPLOADS}/{re.sub(r'[^a-z0-9._-]+', '_', u)[:60]}_{hashlib.sha256(u.encode()).hexdigest()[:10]}"  # readable + collision-free


def safe_filename(name):
    """Basename only, ASCII, conservative characters, allowed extension."""
    base = posixpath.basename(str(name or "").replace("\\", "/"))
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    base = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9._ -]+", "_", base)).strip(" .")[:120] or "document"
    ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
    if ext not in ALLOWED_EXT:
        raise Forbidden(f"type de fichier non autorisé ({ext or 'sans extension'})")
    return base


def can_curate_library(user):
    return user.lower() in LIBRARY_CURATORS


def digest(text):
    return hashlib.sha256((text or "").encode("utf-8", "ignore")).hexdigest()[:16]


def redact(text, keep=300):
    """Trace-safe view of a long text: the beginning, its size and a hash."""
    if TRACE_CONTENT == "full" or not isinstance(text, str) or len(text) <= keep:
        return text
    return f"{text[:keep]}… [{len(text)} caractères, sha256:{digest(text)}]"


class Turns:
    """In-flight turns, scoped to their owner, evicted after completion, with a per-user concurrency cap."""
    TTL = 600

    def __init__(self):
        self._jobs, self._lock = {}, threading.Lock()

    def start(self, tid, user, job, conversation_id=None):
        with self._lock:
            self._evict()
            if conversation_id and any(j.get("conversation_id") == conversation_id and j["status"] == "running" for j in self._jobs.values()):
                raise Busy("une demande est déjà en cours dans cette conversation")
            job["conversation_id"] = conversation_id
            running = sum(1 for j in self._jobs.values() if j["owner"] == user and j["status"] == "running")
            if running >= MAX_RUNNING_TURNS:
                raise Forbidden(f"{running} demandes déjà en cours : attendez qu'elles se terminent")
            job["owner"], job["started"] = user, time.time()
            self._jobs[tid] = job

    def __contains__(self, tid):
        with self._lock:
            return tid in self._jobs

    def get(self, tid, user):
        with self._lock:
            j = self._jobs.get(tid)
            return j if j and j["owner"] == user else None

    def finished(self, tid):
        with self._lock:
            if tid in self._jobs:
                self._jobs[tid]["finished"] = time.time()

    def _evict(self):
        now = time.time()
        for tid in [t for t, j in self._jobs.items() if j.get("finished") and now - j["finished"] > self.TTL]:
            del self._jobs[tid]
