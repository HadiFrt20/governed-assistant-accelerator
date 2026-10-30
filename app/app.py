"""Governed assistant, Databricks App: conversations and history (Lakebase), company library, uploads, Office and HTML generation."""
import os
import re
import threading
import time
import traceback
import uuid

import requests
from databricks.sdk import WorkspaceClient
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

import agent
import docs
import config
import security
import store
from security import Busy, Forbidden

app = FastAPI()
from fastapi.staticfiles import StaticFiles
app.mount("/brand", StaticFiles(directory="static/brand"), name="brand")
app.mount("/vendor", StaticFiles(directory="static/vendor"), name="vendor")  # pinned marked + DOMPurify (no CDN at runtime)
_w = None


def ws():
    global _w
    if _w is None:
        _w = WorkspaceClient()
    return _w
LIVE = security.Turns()  # in-flight jobs (events stream), owner-scoped; persisted to Lakebase on completion
MIME = {"pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf", "html": "text/html; charset=utf-8", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


@app.on_event("startup")
def _init():
    store.init()


def sp_token():
    return ws().config.authenticate()["Authorization"].split(" ", 1)[1]


def user_of(req: Request):
    """Identity set by the Databricks Apps proxy. Outside Apps (local dev only) a fixed developer identity is used."""
    email = req.headers.get("x-forwarded-email")
    if email:
        return email.lower()
    if os.environ.get("LOCAL_DEV") == "1":  # explicit local development only; inside Databricks Apps the header is always set
        return "local@example.com"
    raise HTTPException(401, "identité manquante")


@app.exception_handler(Busy)
def _busy(request: Request, exc: Busy):
    return JSONResponse({"error": str(exc)}, 409)


@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    """CSRF guard: state-changing requests must come from the app's own pages (Origin header, when the browser sends one)."""
    if request.method in ("POST", "PATCH", "PUT", "DELETE"):
        # Fetch Metadata: browsers label every request (Origin on writes, Sec-Fetch-Site); a cross-site page can therefore be refused.
        # Clients that send neither are not browsers (scripts, API calls) and carry no ambient session to abuse.
        origin, site = request.headers.get("origin"), request.headers.get("sec-fetch-site")
        hosts = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", "")} - {""}
        if site in ("cross-site", "same-site") or (origin is not None and origin.split("://", 1)[-1] not in hosts):
            return JSONResponse({"error": "origine non autorisée"}, 403)
        # body size: JSON requests are small; uploads are capped separately (security.MAX_UPLOAD_BYTES)
        limit = security.MAX_UPLOAD_BYTES + 1_000_000 if request.url.path.endswith("upload") else 1_000_000
        if int(request.headers.get("content-length") or 0) > limit:
            return JSONResponse({"error": "requête trop volumineuse"}, 413)
    return await call_next(request)


@app.exception_handler(Forbidden)
def _forbidden(request: Request, exc: Forbidden):
    return JSONResponse({"error": str(exc)}, 403)


def files_api(method, path, **kw):
    return requests.request(method, f"{agent.HOST}/api/2.0/fs/{path}", headers={"Authorization": f"Bearer {sp_token()}"}, timeout=120, **kw)


# Generated pages run model-written script: opaque origin (sandbox) and no network egress except brand fonts.
GENERATED_PAGE_CSP = ("sandbox allow-scripts allow-popups allow-modals allow-downloads; default-src 'none'; script-src 'unsafe-inline'; "
                      "style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src data:; "
                      "connect-src 'none'; form-action 'none'; base-uri 'none'")
# The chat UI: same-origin scripts only (vendored libraries), model content sanitised client-side with DOMPurify.
UI_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
          "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'; "
          "form-action 'self'; frame-ancestors 'none'")


@app.get("/")
def index():
    # the chat UI is a brand-neutral template ({{BRAND}}, reference palette): colours, fonts, names and logos come from the brand pack
    return HTMLResponse(config.rebrand(open("static/index.html", encoding="utf-8").read()),
                        headers={"Content-Security-Policy": UI_CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin"})


@app.get("/api/config")
def cfg(request: Request):
    u = user_of(request)
    return {"brand": config.BRAND, "company": config.COMPANY, "site": config.SITE, "site_short": config.SITE_SHORT, "user": u,
            "name": u.split("@")[0].replace(".", " ").title(),
            "obo": bool(request.headers.get("x-forwarded-access-token")),
            "templates": config.TEMPLATES, "departments": config.DEPARTMENTS}


_LIB = {"at": 0, "items": []}


def library_tree():
    """Library listing, cached 120 s (it is read on every message)."""
    if time.time() - _LIB["at"] < 120:
        return _LIB["items"]
    out = []

    def pages(path):  # the directories API is paginated (next_page_token)
        token = None
        while True:
            r = files_api("GET", f"directories{path}" + (f"?page_token={token}" if token else ""))
            if not r.ok:
                raise RuntimeError(f"listing de la bibliothèque impossible (HTTP {r.status_code})")
            body = r.json()
            yield from body.get("contents", [])
            token = body.get("next_page_token")
            if not token:
                return

    def walk(path):
        for e in pages(path):
            if e.get("is_directory"):
                walk(e["path"])
            else:
                out.append({"path": e["path"], "name": e["name"], "folder": e["path"].removeprefix(config.LIBRARY + "/").rsplit("/", 1)[0] if "/" in e["path"].removeprefix(config.LIBRARY + "/") else "",
                            "size": e.get("file_size"), "modified": e.get("last_modified")})
    try:
        walk(config.LIBRARY)
    except Exception as e:  # any failure (HTTP, network, token): keep the last complete listing rather than failing the request
        print("LIBRARY_ERROR", e, flush=True)
        return _LIB["items"]
    _LIB.update(at=time.time(), items=out)
    return out


@app.get("/api/library")
def library():
    return library_tree()


@app.get("/api/conversations")
def conversations(request: Request):
    return store.list_conversations(user_of(request))


@app.post("/api/conversations")
async def new_conversation(request: Request):
    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    dept = body.get("department")
    return {"id": store.create_conversation(user_of(request), dept if dept in config.DEPARTMENTS else None)}


@app.get("/api/conversations/{cid}")
def conversation(cid: str, request: Request):
    c = store.get_conversation(cid, user_of(request))
    if not c:
        return JSONResponse({"error": "introuvable"}, 404)
    c.pop("llm_messages", None)
    for t in c["turns"]:
        if t["status"] == "running" and t["id"] in LIVE:
            t["live"] = True
    return c


@app.patch("/api/conversations/{cid}")
async def patch_conversation(cid: str, request: Request):
    body = await request.json()
    if "title" in body:
        store.rename(cid, user_of(request), body["title"])
    if "pinned" in body:
        store.set_pinned(cid, user_of(request), body["pinned"])
    return {"ok": True}


@app.get("/api/search")
def search(q: str, request: Request):
    return store.search(user_of(request), q.strip()[:200]) if q.strip() else {"engine": "", "results": []}


@app.delete("/api/conversations/{cid}")
def delete(cid: str, request: Request):
    store.delete(cid, user_of(request))
    return {"ok": True}


@app.post("/api/upload")
def upload(request: Request, file: UploadFile = File(...), conversation_id: str = Form("")):
    user = user_of(request)
    name = security.safe_filename(file.filename)
    data = file.file.read(security.MAX_UPLOAD_BYTES + 1)
    if len(data) > security.MAX_UPLOAD_BYTES:
        raise Forbidden("fichier trop volumineux")
    path = security.confine(f"{security.user_folder(user)}/{uuid.uuid4().hex[:8]}_{name}", security.user_folder(user))
    r = files_api("PUT", f"files{path}?overwrite=false", data=data)
    if not r.ok:
        return JSONResponse({"error": "téléversement refusé"}, r.status_code)
    return {"name": name, "path": path}


@app.post("/api/conversations/{cid}/messages")
async def send(cid: str, request: Request):
    body = await request.json()
    return await run_in_threadpool(_send, cid, request, body)


def _send(cid, request, body):
    user = user_of(request)
    conv = store.get_conversation(cid, user)
    if not conv:
        return JSONResponse({"error": "introuvable"}, 404)
    text, atts = str(body.get("text", "")), body.get("attachments", []) or []
    if not text.strip() or len(text) > security.MAX_PROMPT_CHARS:
        raise Forbidden(f"message vide ou trop long (maximum {security.MAX_PROMPT_CHARS} caractères)")
    # attachments must be the user's own uploads: paths are re-validated, never trusted from the client
    # attachments: the user's own uploads or documents of the shared library; paths are re-validated, never trusted from the client
    atts = [{"name": security.safe_filename(a.get("name")), "path": security.confine(a.get("path"), security.user_folder(user), config.LIBRARY)} for a in atts[:5]]
    if not conv["turns"]:
        store.rename(cid, user, text[:80])
    content = text + ("".join(f"\n[Fichier joint : {a['path']}]" for a in atts))
    history = list(conv["llm_messages"])
    job = {"events": [], "files": [], "answer": None, "status": "running", "turn_start": len(history),
           "messages": history + [{"role": "user", "content": content}]}
    user_token = request.headers.get("x-forwarded-access-token") if config.S.get("obo", True) else None
    st = sp_token()
    listing = "\n".join(f"- {d['path']}" for d in library_tree())
    tid = uuid.uuid4().hex
    LIVE.start(tid, user, job, conversation_id=cid)
    try:
        store.start_turn(cid, text, atts, tid=tid)
    except Exception:  # never leave a registered job "running" when the turn could not even be created
        job["status"] = "done"
        LIVE.finished(tid)
        raise
    # follow-up data questions continue the same Genie One conversation (only threads owned by the user)
    genie_conv = next((e.get("conversation_id") for t in reversed(conv["turns"]) for e in reversed(t.get("events") or [])
                       if e.get("type") == "handoff" and e.get("own")), None)

    def work():
        try:
            agent.run_agent(job, user_token, st, user=user, user_folder=security.user_folder(user), department=conv.get("department"), library_listing=listing,
                            conversation_id=cid, turn_id=tid, genie_conv=genie_conv, has_attachments=bool(atts), text=text,
                            previous=(conv["turns"][-1]["user_text"] if conv["turns"] else None), attachments=atts)
        except (PermissionError, Forbidden) as e:  # expected refusals: tell the user exactly what to do
            job["events"].append({"type": "error", "text": str(e)})
            job["answer"] = str(e)
        except Exception as e:  # unexpected: details in the logs and the trace, a generic message for the user
            traceback.print_exc()
            job["events"].append({"type": "error", "text": "Erreur technique"})
            job["answer"] = job["answer"] or f"Une erreur technique est survenue ({type(e).__name__}). Relancez la demande ; si elle persiste, contactez l'équipe Data & IA."
        if not agent.history_is_complete(job["messages"]):
            # the turn did not complete: keep the stored history a valid exchange (no dangling user message or tool round)
            del job["messages"][job["turn_start"]:]
        try:
            for f in job["files"]:
                try:  # one file record failing must not lose the turn (the file stays in the volume)
                    store.add_file(user, cid, f)
                except Exception:
                    traceback.print_exc()
            store.finish_turn(tid, cid, job["answer"], [e for e in job["events"] if e["type"] != "progress"], job["messages"])
        except Exception:
            traceback.print_exc()
            try:
                store.fail_turn(tid, "La réponse n'a pas pu être enregistrée (incident de stockage). Relancez la demande.")
            except Exception:
                traceback.print_exc()
        finally:
            job["status"] = "done"
            LIVE.finished(tid)

    threading.Thread(target=work, daemon=True).start()
    return {"turn_id": tid}


@app.get("/api/turns/{tid}")
def poll(tid: str, request: Request, since: int = 0):
    j = LIVE.get(tid, user_of(request))
    if not j:
        return JSONResponse({"error": "terminé ou introuvable"}, 404)
    return {"status": j["status"], "events": j["events"][since:], "next": len(j["events"]), "answer": j["answer"], "files": j["files"]}


@app.get("/api/files")
def my_files(request: Request):
    return store.list_files(user_of(request))


@app.get("/api/files/{name}")
def download(name: str, request: Request):
    owner = store.file_owner(name, user_of(request))
    if not owner:
        return JSONResponse({"error": "accès refusé"}, 403)
    r = files_api("GET", f"files{owner['path']}")
    if not r.ok:
        return JSONResponse({"error": "fichier indisponible"}, 502 if r.status_code >= 500 else 404)
    if name.endswith(".html"):  # generated pages open in the browser, sandboxed away from the assistant's origin
        return Response(r.content, media_type=MIME["html"], headers={"Content-Disposition": "inline",
                        "Content-Security-Policy": GENERATED_PAGE_CSP, "X-Content-Type-Options": "nosniff"})
    return Response(r.content, media_type=MIME.get(name.rsplit(".", 1)[-1], "application/octet-stream"),
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/library/download")
def library_download(path: str):
    path = security.confine(path, config.LIBRARY)
    r = files_api("GET", f"files{path}")
    if not r.ok:
        return JSONResponse({"error": "introuvable"}, 404)
    name = path.rsplit("/", 1)[-1]
    return Response(r.content, media_type=MIME.get(name.rsplit(".", 1)[-1], "application/octet-stream"),
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/library/upload")
def library_upload(request: Request, file: UploadFile = File(...), folder: str = Form("General")):
    """The library is trusted context for every user: only curators (security.library_curators) may add documents, never overwrite."""
    if not security.can_curate_library(user_of(request)):
        raise Forbidden("seuls les responsables de la bibliothèque peuvent y ajouter des documents")
    folder = re.sub(r"[^A-Za-z0-9_-]+", "_", folder)[:40] or "General"
    data = file.file.read(security.MAX_UPLOAD_BYTES + 1)
    if len(data) > security.MAX_UPLOAD_BYTES:
        raise Forbidden("fichier trop volumineux")
    path = security.confine(f"{config.LIBRARY}/{folder}/{security.safe_filename(file.filename)}", config.LIBRARY)
    r = files_api("PUT", f"files{path}?overwrite=false", data=data)
    if r.status_code == 409:
        raise Forbidden("un document porte déjà ce nom")
    if not r.ok:
        return JSONResponse({"error": "ajout refusé"}, r.status_code)
    _LIB["at"] = 0  # refresh the cached listing
    return {"ok": True, "path": path}
