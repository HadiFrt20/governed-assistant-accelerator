"""Governed Assistant Accelerator installer.

Deploys the assistant into a Databricks workspace from config/assistant.yaml + brands/<brand>/brand.yaml, idempotently:
re-running it updates what this installer owns and skips what is already in place.

    uv run --with pyyaml --with databricks-sdk --with python-pptx --with python-docx \
        python install/install.py --config config/assistant.yaml [--only uc,policies] [--dry-run] [--force]

Steps: uc · policies · lakebase · mlflow · usage · build · preflight · app · smoke (see STEPS at the bottom).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service.sql import StatementState

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings as settings_mod  # noqa: E402
from settings import SENSITIVE_CATEGORIES  # noqa: E402

JUDGE_MODEL = "system.ai.gpt-5-4-mini"   # model used by the built-in jailbreak / unsafe-content guardrails
APP_SCOPES = ["ai-gateway", "sql", "dashboards.genie", "files.files", "serving.serving-endpoints"]
MARKER = "[governed-assistant-accelerator]"  # in the comment of every model service this installer owns
APP_DEPS = ["pytest", "pyyaml", "requests", "python-pptx", "python-docx", "pypdf", "fastapi", "httpx", "python-multipart", "psycopg[binary]", "databricks-sdk"]


def log(step, status, msg=""):
    print(f"[{step}] {status}" + (f" · {msg}" if msg else ""), flush=True)


def _q(s):
    """Escape a value for a single-quoted Databricks SQL string literal (backslash escapes, not doubled quotes)."""
    return str(s).replace("\\", "\\\\").replace("'", "\\'")


class Ctx:
    """Configuration, brand and workspace clients shared by every step."""

    def __init__(self, config_path, dry_run=False, force=False):
        self.cfg = yaml.safe_load(open(config_path, encoding="utf-8"))
        self.brand_dir = ROOT / "brands" / self.cfg["brand"]
        self.brand = yaml.safe_load(open(self.brand_dir / "brand.yaml", encoding="utf-8"))
        self.dry, self.force = dry_run, force
        ws = self.cfg["workspace"]
        self.profile, self.warehouse = ws["profile"], ws["warehouse_id"]
        self.w = WorkspaceClient(profile=self.profile)
        uc = self.cfg["unity_catalog"]
        self.catalog, self.schema, self.volume = uc["catalog"], uc["schema"], uc["volume"]
        self.fq = f"{self.catalog}.{self.schema}"
        self.files_root = f"/Volumes/{self.catalog}/{self.schema}/{self.volume}"
        self.app = self.cfg["app"]["name"]
        self.lb_catalog = re.sub(r"[^a-z0-9]+", "_", self.app.lower()) + "_lb"
        self.build_dir = ROOT / "build" / self.app
        self.state_path = ROOT / "state" / f"{self.app}.json"  # ids and URLs only, never tokens
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        self._me = None

    @property
    def me(self):
        if not self._me:
            self._me = self.w.current_user.me().user_name
        return self._me

    def save(self, **kv):
        self.state.update(kv)
        self.state_path.parent.mkdir(exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=1, ensure_ascii=False))

    def api(self, method, path, body=None, query=None):
        return self.w.api_client.do(method, path, query=query, body=body)

    def sql(self, stmt, fetch=False):
        """Run one statement on the configured warehouse; raise with the warehouse error message on failure."""
        if self.dry:
            print("   SQL", stmt.strip().splitlines()[0][:110])
            return []
        r = self.w.statement_execution.execute_statement(statement=stmt, warehouse_id=self.warehouse, wait_timeout="50s")
        while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
            time.sleep(2)
            r = self.w.statement_execution.get_statement(r.statement_id)
        if r.status.state != StatementState.SUCCEEDED:
            raise RuntimeError(f"SQL failed: {stmt.strip().splitlines()[0][:100]} → {r.status.error.message[:500]}")
        return (r.result.data_array or []) if fetch and r.result else []

    def cli(self, *args, check=True):
        """Databricks CLI with the configured profile; returns parsed JSON. In dry-run, only read commands run."""
        read_only = len(args) > 1 and args[1].startswith(("get", "list"))
        if self.dry and not read_only:
            print("   CLI", " ".join(args)[:160])
            return {}
        p = subprocess.run(["databricks", *args, "-p", self.profile, "-o", "json"], capture_output=True, text=True)
        if p.returncode != 0:
            if check:
                raise RuntimeError(f"databricks {' '.join(args[:3])} failed: {(p.stderr or p.stdout)[:600]}")
            return None
        try:
            return json.loads(p.stdout) if p.stdout.strip() else {}
        except json.JSONDecodeError:
            return {"raw": p.stdout}

    def service_fq(self, role):
        m = self.cfg["models"].get(role)
        return f"{self.fq}.{m['service']}" if m else None

    def app_sp(self):
        """Service principal (client id) of the app; creates the app without compute if it does not exist yet."""
        a = self.cli("apps", "get", self.app, check=False)
        if not a:
            log("app", "create", self.app)
            self.cli("apps", "create", "--no-compute", "--no-wait", "--json", json.dumps({"name": self.app, "description": self.brand["name"]}))
            a = self.cli("apps", "get", self.app) or {}
        return a.get("service_principal_client_id"), a


# ---------- 1. Unity Catalog ----------
def step_uc(c):
    schema_comment = c.brand["name"] + " : services IA gouvernés, fichiers, traces et couche d'usage"
    c.sql(f"CREATE SCHEMA IF NOT EXISTS {c.fq} COMMENT '{_q(schema_comment)}'")
    c.sql(f"CREATE VOLUME IF NOT EXISTS {c.fq}.{c.volume} COMMENT 'Bibliothèque documentaire, documents générés et fichiers téléversés'")
    for sub in ("library", "generated", "uploads"):
        if not c.dry:
            c.w.files.create_directory(f"{c.files_root}/{sub}")
    log("uc", "ok", f"{c.fq} · volume {c.files_root} (library, generated, uploads)")


# ---------- 2. Policies + governed model services ----------
def with_deploy_gate(steps):
    """The test gate always runs right before the app is deployed, even with --only app."""
    steps = list(steps)
    if "app" in steps and "preflight" not in steps:
        steps.insert(steps.index("app"), "preflight")
    if "app" in steps and steps.index("preflight") > steps.index("app"):
        steps.remove("preflight")
        steps.insert(steps.index("app"), "preflight")
    return steps


def governance_drift(conf, policies, rate_limits):
    """True when the service's policies, rate limits or inference table differ from the desired governance."""
    have = sorted((p["name"], json.dumps(p.get("options", {}), sort_keys=True)) for p in conf.get("service_policies", []) if not p.get("is_deleted"))
    want = sorted((p["name"], json.dumps(p.get("options", {}), sort_keys=True)) for p in policies)
    have_rl = [(r.get("key"), str(r.get("requests"))) for r in conf.get("rate_limits", [])]
    return have != want or have_rl != [(r["key"], str(r["requests"])) for r in rate_limits] or not conf.get("inference_table")


def installer_owns(service, status):
    """True if this installer created the model service (marker in its comment) or just created it."""
    return status == "created" or MARKER in ((service or {}).get("comment") or "")


def may_manage(service, status, force):
    """Governance (policies, rate limits, inference table) is only rewritten on services this installer owns, or with --force."""
    return installer_owns(service, status) or bool(force)


def desired_policies(c):
    g = c.cfg["governance"]
    pols = [{"name": g["ip_leak_policy"]["name"], "policy_type": "POLICY_TYPE_CUSTOM", "handler": f"functions/{c.fq}.policy_block_ip_leak", "rank": 0}]
    b = g.get("builtin_policies", {})
    if b.get("jailbreak", True):
        pols.append({"name": "anti-jailbreak", "policy_type": "POLICY_TYPE_BUILTIN", "handler": "system.ai.block_jailbreak", "rank": len(pols),
                     "options": {"dry_run": "false", "phases": "pre_call", "model_service": JUDGE_MODEL}})
    if b.get("unsafe_content", True):
        pols.append({"name": "contenu-inapproprie", "policy_type": "POLICY_TYPE_BUILTIN", "handler": "system.ai.block_unsafe_content", "rank": len(pols),
                     "options": {"dry_run": "false", "phases": "pre_call,post_call", "model_service": JUDGE_MODEL}})
    sd = b.get("sensitive_data")
    if sd:
        cats = sd.get("categories", [])
        unknown = [x for x in cats if x not in SENSITIVE_CATEGORIES]
        if unknown:
            raise SystemExit(f"governance.builtin_policies.sensitive_data.categories: unknown {unknown}. Allowed: {sorted(SENSITIVE_CATEGORIES)}")
        if sd.get("action", "transform") not in ("transform", "block"):
            raise SystemExit("sensitive_data.action must be transform (anonymise) or block; ask is only supported on MCP services")
        pols.append({"name": "donnees-sensibles", "policy_type": "POLICY_TYPE_BUILTIN", "handler": "system.ai.detect_sensitive_data", "rank": len(pols),
                     "options": {"dry_run": "false", "phases": "pre_call,post_call", "categories": ",".join(cats), "action": sd.get("action", "transform")}})
    return pols


def price_cases(pricing):
    """SQL CASE branches for the dashboard cost: USD per million tokens, matched anywhere in the gateway model name."""
    return " ".join(f"WHEN destination_model LIKE '%{_q(m)}%' THEN COALESCE(input_tokens, 0) * {float(p[0])} / 1e6 + COALESCE(output_tokens, 0) * {float(p[1])} / 1e6"
                    for m, p in pricing.items() if m != "default")


def ip_leak_sql(fq, keywords, reason, company):
    """CREATE FUNCTION for the custom DENY policy (CEL-transpilable: CONTAINS(LOWER(...)) only, every branch has a reason)."""
    cond = "\n        OR ".join(f"CONTAINS(LOWER(event:context.message::string), '{_q(k.lower())}')" for k in keywords)
    comment = f"{company} : bloque l'envoi de propriété intellectuelle vers un LLM (mots-clés : {', '.join(keywords)})."
    return (Path(__file__).parent / "sql" / "policy_ip_leak.sql").read_text().format(fq=fq, conditions=cond, reason=_q(reason), comment=_q(comment))


def step_policies(c):
    ip = c.cfg["governance"]["ip_leak_policy"]
    kws = [k.lower() for k in ip["keywords"]]
    if not kws:
        raise SystemExit("governance.ip_leak_policy.keywords must not be empty")
    c.sql(ip_leak_sql(c.fq, kws, ip["reason"], c.brand["company"]))
    log("policies", "ok", f"function {c.fq}.policy_block_ip_leak ({len(kws)} keywords)")

    pols = desired_policies(c)
    rl = [{"key": "RATE_LIMIT_KEY_USER_DEFAULT", "renewal_period": "RATE_LIMIT_RENEWAL_PERIOD_MINUTE",
           "requests": str(c.cfg["governance"].get("rate_limit_per_user_per_minute", 60))}]
    for role in ("assistant", "developer"):
        m = c.cfg["models"].get(role)
        if not m:
            continue
        fq, svc = c.service_fq(role), m["service"]
        comment = f"{c.brand['name']} - {svc} ({m['model'].split('.')[-1]}) {MARKER}"
        try:
            cur, status = c.api("GET", f"/api/2.1/unity-catalog/model-services/{fq}"), "skip"
        except NotFound:
            cur, status = None, "created"
            body = {"comment": comment,
                    "config": {"routing": {"destinations": [{"name": m["model"].split(".")[-1].removeprefix("databricks-"),
                                                            "destination_type": "DESTINATION_TYPE_PAY_PER_TOKEN_FOUNDATION_MODEL", "traffic_percentage": 100,
                                                            "pay_per_token_config": {"model": f"models/{m['model']}"}}]}}}
            if not c.dry:
                cur = c.api("POST", "/api/2.1/unity-catalog/model-services", body=body, query={"parent": f"schemas/{c.fq}", "model_service_id": svc})
        # never rewrite the governance of a service this installer does not own, unless --force (which also adopts it)
        owned = installer_owns(cur, status)
        if not may_manage(cur, status, c.force):
            log("policies", "skip", f"{fq} exists and was not created by this installer: governance left untouched (--force to adopt it)")
            continue
        if governance_drift((cur or {}).get("config", {}), pols, rl) or not owned:
            patch = {"comment": comment, "config": {"service_policies": pols, "rate_limits": rl,
                                                    "inference_table": {"parent": f"schemas/{c.fq}", "table_name_prefix": svc}}}
            if not c.dry:
                c.api("PATCH", f"/api/2.1/unity-catalog/model-services/{fq}", body=patch,
                      query={"update_mask": "comment,config.service_policies,config.inference_table,config.rate_limits"})
            status = "updated" if status == "skip" else status
        log("policies", status, f"model service {fq} · {len(pols)} policies · {rl[0]['requests']} req/min/user · inference table")
    c.save(services={r: c.service_fq(r) for r in ("assistant", "developer") if c.cfg["models"].get(r)})


# ---------- 3. Lakebase (conversations, pins, BM25 search) ----------
def _endpoint(c, branch):
    eps = c.cli("postgres", "list-endpoints", branch, check=False) or []
    eps = eps if isinstance(eps, list) else eps.get("endpoints", [])
    return next((e for e in eps if e.get("status", {}).get("endpoint_type") == "ENDPOINT_TYPE_READ_WRITE"), eps[0] if eps else {})


def step_lakebase(c):
    proj, branch = f"projects/{c.app}", f"projects/{c.app}/branches/production"
    if c.cli("postgres", "get-project", proj, check=False):
        status = "skip"
    else:
        c.cli("postgres", "create-project", c.app, "--json", json.dumps({"spec": {"display_name": f"{c.brand['name']} - état applicatif", "pg_version": 17}}))
        status = "created"
    if c.dry:
        log("lakebase", "dry-run", proj)
        return
    # wait until the read-write endpoint is provisioned and exposes a host; fail loudly otherwise
    deadline = time.time() + 600
    while True:
        ep = _endpoint(c, branch)
        st = ep.get("status", {})
        if st.get("current_state") in ("ACTIVE", "IDLE") and (st.get("hosts") or {}).get("host"):
            break
        if time.time() > deadline:
            raise RuntimeError(f"Lakebase endpoint of {proj} not ready after 10 min (state {st.get('current_state')}, hosts {st.get('hosts')})")
        time.sleep(10)
    dbs = c.cli("postgres", "list-databases", branch) or []
    dbs = dbs if isinstance(dbs, list) else dbs.get("databases", [])
    db = next((d for d in dbs if d.get("status", {}).get("postgres_database") == "databricks_postgres"), None)
    if not db:
        raise RuntimeError(f"database databricks_postgres not found on {branch}")
    lb = {"project": proj, "branch": branch, "endpoint": ep["name"], "host": st["hosts"]["host"], "database": "databricks_postgres", "database_resource": db["name"]}
    log("lakebase", status, f"{proj} · endpoint {lb['endpoint']} ({st['current_state']}) · {lb['host']}")
    if c.cli("postgres", "get-catalog", f"catalogs/{c.lb_catalog}", check=False):
        log("lakebase", "skip", f"UC catalog {c.lb_catalog}")
    else:
        c.cli("postgres", "create-catalog", c.lb_catalog, "--json", json.dumps({"spec": {"postgres_database": "databricks_postgres", "branch": branch}}))
        log("lakebase", "created", f"UC catalog {c.lb_catalog} (history exposed to the gold layer)")
    c.save(lakebase=lb)


# ---------- 4. MLflow tracing in Unity Catalog ----------
MLFLOW_SCRIPT = r'''
import json, sys, mlflow
from mlflow.entities.trace_location import UnityCatalog
mlflow.set_tracking_uri("databricks")
name, catalog, schema = sys.argv[1:4]
client = mlflow.MlflowClient()
def ensure(n):
    e = client.get_experiment_by_name(n)
    return e.experiment_id if e else client.create_experiment(n)
eid = ensure(name)
exp = client.get_experiment(eid)
linked = bool(getattr(exp, "trace_location", None)) or any("trace" in k.lower() and "location" in k.lower() for k in (exp.tags or {}))
status = "skip"
if not linked:
    if mlflow.search_traces(locations=[eid], max_results=1, return_type="list"):
        # a UC destination can only be linked to an experiment without traces
        name, status = name + "-uc", "created-uc-copy"
        eid = ensure(name)
    try:
        mlflow.set_experiment(experiment_id=eid, trace_location=UnityCatalog(catalog_name=catalog, schema_name=schema, table_prefix="assistant"))
        status = "linked" if status == "skip" else status
    except Exception as ex:
        if "already" not in str(ex).lower():
            raise
print(json.dumps({"experiment_id": eid, "name": name, "status": status}))
'''


def step_mlflow(c):
    name = c.cfg["observability"]["mlflow_experiment"]
    if c.dry:
        log("mlflow", "dry-run", name)
        return
    env = {**os.environ, "DATABRICKS_CONFIG_PROFILE": c.profile, "MLFLOW_TRACING_SQL_WAREHOUSE_ID": c.warehouse, "MLFLOW_DISABLE_AGENT_HINT": "1"}
    with tempfile.TemporaryDirectory() as d:
        script = Path(d) / "link_experiment.py"
        script.write_text(MLFLOW_SCRIPT)
        p = subprocess.run(["uv", "run", "-q", "--with", "mlflow-skinny[databricks]", "python", "-I", str(script), name, c.catalog, c.schema],
                           capture_output=True, text=True, env=env)
    line = next((l for l in reversed(p.stdout.splitlines()) if l.startswith("{")), None)
    if p.returncode or not line:
        raise RuntimeError(f"mlflow step failed: {p.stderr[-800:]}")
    r = json.loads(line)
    log("mlflow", r["status"], f"experiment {r['name']} ({r['experiment_id']}) → traces in {c.fq}.assistant_otel_*")
    c.save(experiment_id=r["experiment_id"], experiment_name=r["name"])


# ---------- 5. Build the app bundle (generic code + settings.json + brand assets) ----------
def build_bundle(c, out):
    """Assemble a deployable app in `out` (which must not exist yet)."""
    shutil.copytree(ROOT / "app", out, ignore=shutil.ignore_patterns("__pycache__", "*.log", "settings.json", "app.yaml"))
    brand_out = out / "static" / "brand"
    if brand_out.exists():
        shutil.rmtree(brand_out)
    brand_out.mkdir(parents=True)
    for f in set(c.brand["logos"].values()):
        shutil.copy(c.brand_dir / f, brand_out / f)
    import office_templates  # brand → PowerPoint and Word templates; an ImportError aborts the build (no silent fallback)
    tpl = office_templates.build(c.brand, c.brand_dir, out)
    c.save(templates=tpl)
    s = settings_mod.build(c.cfg, c.brand, c.state, None if c.dry else c.me)  # raises if a template key is missing
    (out / "settings.json").write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
    env = [("SETTINGS_FILE", "settings.json"), ("MLFLOW_TRACKING_URI", "databricks"),
           ("MLFLOW_EXPERIMENT_ID", s["observability"]["experiment_id"]), ("MLFLOW_TRACING_SQL_WAREHOUSE_ID", c.warehouse),
           ("LAKEBASE_ENDPOINT", s["lakebase"]["endpoint"] or ""), ("PGHOST_FALLBACK", s["lakebase"]["host"] or "")]
    (out / "app.yaml").write_text('command: ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "$DATABRICKS_APP_PORT"]\nenv:\n' +
                                  "".join(f"  - name: {k}\n    value: {json.dumps(str(v))}\n" for k, v in env))
    return tpl


def step_build(c):
    """Build next to the target, then swap: a failed build never leaves a half-written bundle behind."""
    c.build_dir.parent.mkdir(exist_ok=True)
    tmp = c.build_dir.with_name(f".{c.app}.new")
    old = c.build_dir.with_name(f".{c.app}.old")
    for d in (tmp, old):
        if d.exists():
            shutil.rmtree(d)
    try:
        tpl = build_bundle(c, tmp)
        if c.build_dir.exists():
            c.build_dir.rename(old)
        tmp.rename(c.build_dir)
    finally:
        for d in (tmp, old):
            if d.exists():
                shutil.rmtree(d)
    log("build", "ok", f"{c.build_dir.relative_to(ROOT)} · brand {c.brand['name']} · Office templates ({', '.join(tpl)}) · settings.json + app.yaml")


# ---------- 6. Preflight: the test suite must pass before any deploy ----------
def step_preflight(c):
    if not c.build_dir.exists():
        raise SystemExit("preflight: run the build step first")
    with tempfile.TemporaryDirectory() as d:
        probe = Path(d) / c.app
        shutil.copytree(c.build_dir, probe)
        env = {**os.environ, "ACCELERATOR_BUILD_DIR": str(probe), "SETTINGS_FILE": str(probe / "settings.json"),
               "DATABRICKS_HOST": os.environ.get("DATABRICKS_HOST", "https://preflight.invalid")}
        cmd = ["uv", "run", "-q", "--python", "3.11", "--with-requirements", str(ROOT / "requirements-dev.txt"),
               "python", "-I", "-m", "pytest", str(ROOT / "tests"), "-q", "-p", "no:cacheprovider"]
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env)
    out = (p.stdout + p.stderr).strip()
    tail = out.splitlines()[-1] if out else "(no output)"
    if p.returncode != 0:
        print(out[-3000:])
        raise SystemExit(f"preflight FAILED: {tail} — nothing deployed")
    log("preflight", "ok", tail)


# ---------- 7. Databricks App + grants ----------
def app_settings_drift(app, want):
    """True when the app's OBO scopes or its Lakebase resource differ from the desired ones (other resources are left alone)."""
    if sorted(app.get("user_api_scopes") or []) != sorted(want["user_api_scopes"]):
        return True
    have = {r.get("name"): r.get("postgres") for r in app.get("resources") or []}
    return any(have.get(r["name"]) != r["postgres"] for r in want["resources"])


def step_app(c):
    sp, a = c.app_sp()
    lb = c.state.get("lakebase") or {}
    if not lb.get("host"):
        raise SystemExit("app: Lakebase not provisioned (run the lakebase step)")
    want = {"user_api_scopes": APP_SCOPES if c.cfg["app"].get("obo", True) else [],
            "resources": [{"name": "postgres", "postgres": {"branch": lb["branch"], "database": lb["database_resource"], "permission": "CAN_CONNECT_AND_CREATE"}}]}
    if app_settings_drift(a, want):
        c.cli("apps", "update", c.app, "--json", json.dumps({"name": c.app, "description": c.brand["name"], **want}))
        log("app", "updated", "OBO scopes + Lakebase resource")
    grant_access(c, sp)
    # dedicated folder owned by the installer; import-dir (not sync --full: no remote deletes, and build/ is git-ignored)
    src = f"/Workspace/Users/{c.me}/governed-assistant/{c.app}"
    if not c.dry:
        p = subprocess.run(["databricks", "workspace", "import-dir", str(c.build_dir), src, "--overwrite", "-p", c.profile], capture_output=True, text=True)
        if p.returncode:
            raise RuntimeError(f"upload failed: {p.stderr[-600:]}")
        if (c.cli("apps", "get", c.app).get("compute_status") or {}).get("state") not in ("ACTIVE", "STARTING"):
            c.cli("apps", "start", c.app)
    d = c.cli("apps", "deploy", c.app, "--source-code-path", src)
    log("app", "deployed", f"{(d.get('status') or {}).get('state', '')} {(d.get('status') or {}).get('message', '')}")
    c.save(app_url=(c.cli("apps", "get", c.app) or {}).get("url"), app_sp=sp)


def sp_grants(c, trace_tables):
    """Least privilege for the app identity: no schema-wide SELECT/MODIFY; MODIFY only on trace tables; views read-only."""
    grants = [("CATALOG", c.catalog, ["USE_CATALOG"]), ("SCHEMA", c.fq, ["USE_SCHEMA"]),
              ("VOLUME", f"{c.fq}.{c.volume}", ["READ_VOLUME", "WRITE_VOLUME"]), ("FUNCTION", f"{c.fq}.policy_block_ip_leak", ["EXECUTE"])]
    grants += [("TABLE", f"{c.fq}.{t}", ["SELECT"] if "VIEW" in (kind or "") else ["SELECT", "MODIFY"]) for t, kind in trace_tables]
    grants += [("MODEL_SERVICE", c.service_fq(r), ["EXECUTE"]) for r in ("assistant", "developer") if c.cfg["models"].get(r)]
    return grants


def grant_access(c, sp):
    """Narrow grants. The app identity reads the library and writes generated/ and uploads/ (UC volume privileges are
    volume-wide, see docs/SECURITY.md), writes MLflow spans to the trace tables, and calls the services only when OBO is off."""
    trace_tables = [] if c.dry else c.sql(
        f"SELECT table_name, table_type FROM {c.catalog}.information_schema.tables WHERE table_schema = '{_q(c.schema)}' "
        f"AND (table_name LIKE 'assistant\\_otel\\_%' OR table_name = 'assistant_trace_metadata')", fetch=True)
    if not trace_tables and not c.dry and c.state.get("experiment_id"):
        # linking the experiment to UC creates these tables; if they are missing, spans would be dropped silently
        log("app", "warn", "no assistant_otel_* tables yet: run the mlflow step, then `--only app` again, or traces will not be written")
    for typ, name, privs in sp_grants(c, trace_tables):
        c.cli("grants", "update", typ, name, "--json", json.dumps({"changes": [{"principal": sp, "add": privs}]}))
    # earlier installer versions granted SELECT/MODIFY on the whole schema: narrow it back
    c.cli("grants", "update", "SCHEMA", c.fq, "--json", json.dumps({"changes": [{"principal": sp, "remove": ["SELECT", "MODIFY"]}]}), check=False)
    users = (c.cfg.get("access") or {}).get("users_group")
    if users:  # with OBO the model and Genie calls run as the user: users need EXECUTE on the services (no volume access needed)
        for typ, name, privs in [("CATALOG", c.catalog, ["USE_CATALOG"]), ("SCHEMA", c.fq, ["USE_SCHEMA"])] + \
                [("MODEL_SERVICE", c.service_fq(r), ["EXECUTE"]) for r in ("assistant", "developer") if c.cfg["models"].get(r)]:
            c.cli("grants", "update", typ, name, "--json", json.dumps({"changes": [{"principal": users, "add": privs}]}))
    if c.state.get("experiment_id") and not c.dry:
        # PATCH adds one ACL entry; PUT (set-permissions) would replace the experiment's whole ACL
        c.api("PATCH", f"/api/2.0/permissions/experiments/{c.state['experiment_id']}",
              body={"access_control_list": [{"service_principal_name": sp, "permission_level": "CAN_MANAGE"}]})
    log("app", "ok", f"grants for {sp}: catalog/schema usage, volume, policy function, {len(trace_tables)} trace tables, model services, experiment"
        + (f" · group {users}: EXECUTE on services" if users else ""))


# ---------- 8. Usage gold layer + hourly job + AI/BI dashboard ----------
def step_usage(c):
    sp, _ = c.app_sp()
    workspace_id = str(c.w.get_workspace_id())
    # destination_model carries a provider prefix (databricks-…), so match the pricing key anywhere in the name
    cases = price_cases(c.cfg.get("pricing", {}))
    sql = (Path(__file__).parent / "sql" / "gold_usage.sql").read_text().format(
        name=c.brand["name"], name_sql=_q(c.brand["name"]), company_sql=_q(c.brand["company"]), app=c.app, fq=c.fq, lb=c.lb_catalog,
        assistant_fq=c.service_fq("assistant"), developer_fq=c.service_fq("developer") or "__none__", sp=sp, workspace_id=workspace_id,
        price_cases=cases or "WHEN false THEN 0.0")
    gen = ROOT / "build" / f"{c.app}-gold_usage.sql"
    gen.parent.mkdir(exist_ok=True)
    gen.write_text(sql, encoding="utf-8")

    # static snapshot of the guardrail configuration (dashboard "Technique" tab)
    rows, desc = [], settings_mod.policy_descriptions(c.cfg)
    for role in ("assistant", "developer"):
        fq = c.service_fq(role)
        if not fq or c.dry:
            continue
        cfg = c.api("GET", f"/api/2.1/unity-catalog/model-services/{fq}")["config"]
        model = ", ".join(d["pay_per_token_config"]["model"].split(".")[-1].removeprefix("databricks-") for d in cfg["routing"]["destinations"])
        rl = "; ".join(f"{r['requests']} req/min (user)" for r in cfg.get("rate_limits", []))
        it = (cfg.get("inference_table") or {}).get("table", "").removeprefix("tables/")
        for p in cfg.get("service_policies", []):
            o = p.get("options", {})
            rows.append((fq, model, p["name"], desc.get(p["name"], p["name"]),
                         "Personnalisée (SQL UC)" if p["policy_type"] == "POLICY_TYPE_CUSTOM" else "Intégrée Databricks",
                         p["handler"].removeprefix("functions/"), o.get("phases", "pre_call,post_call"), o.get("action", "block"), int(p.get("rank", 0)), rl, it))
    if rows:
        vals = ",\n".join("(" + ", ".join(str(v) if isinstance(v, int) else f"'{_q(v)}'" for v in r) + ")" for r in rows)
        c.sql(f"CREATE OR REPLACE TABLE {c.fq}.ref_guardrail_config COMMENT 'Configuration Unity AI Gateway (instantané à l\\'installation)' AS "
              f"SELECT * FROM VALUES {vals} AS t(service, modele, politique, description, type_politique, implementation, phases, action, rang, limite_debit, table_inference)")
    log("usage", "ok", f"gold SQL rendered ({gen.relative_to(ROOT)}) · ref_guardrail_config {len(rows)} rows")

    # hourly job running the gold SQL from the workspace
    ws_sql = f"/Workspace/Users/{c.me}/governed-assistant/{c.app}-jobs/gold_usage.sql"
    if not c.dry:
        c.cli("workspace", "mkdirs", ws_sql.rsplit("/", 1)[0])
        subprocess.run(["databricks", "workspace", "import", ws_sql, "--file", str(gen), "--format", "RAW", "--overwrite", "-p", c.profile], check=True, capture_output=True)
    job = {"name": f"{c.app}-usage", "max_concurrent_runs": 1, "tags": {"project": c.app},
           "schedule": {"quartz_cron_expression": c.cfg["observability"].get("usage_job_cron", "0 5 * * * ?"), "timezone_id": "Europe/Paris", "pause_status": "UNPAUSED"},
           "tasks": [{"task_key": "refresh_gold", "sql_task": {"file": {"path": ws_sql, "source": "WORKSPACE"}, "warehouse_id": c.warehouse}}]}
    jid = c.state.get("job_id")
    if jid and c.cli("jobs", "get", str(jid), check=False):
        c.cli("jobs", "reset", "--json", json.dumps({"job_id": jid, "new_settings": job}))
        log("usage", "updated", f"job {job['name']} ({jid})")
    else:
        jid = c.cli("jobs", "create", "--json", json.dumps(job)).get("job_id")
        log("usage", "created", f"job {job['name']} ({jid})")
    c.save(job_id=jid)
    # refresh only once the app has created its history tables in Lakebase
    ready = False
    if not c.dry:
        try:
            ready = bool(c.sql(f"SHOW TABLES IN {c.lb_catalog}.assistant LIKE 'turns'", fetch=True))
        except RuntimeError:
            ready = False
    if ready:
        c.cli("jobs", "run-now", str(jid), "--no-wait")
        log("usage", "ok", "refresh started")
    else:
        log("usage", "skip", "history tables not created yet (the app creates them on first start); the hourly job will pick them up")

    import dashboard
    ser = dashboard.build(c.fq, c.brand["name"], c.brand["company"], c.brand.get("site_short") or c.brand.get("site", ""), c.brand["colors"],
                          c.brand.get("fonts", {}).get("body", "Inter"))
    title = f"{c.brand['name']} — Usage et gouvernance IA"
    if c.dry:
        log("usage", "dry-run", title)
        return
    did, st = c.state.get("dashboard_id"), "updated"
    if did:
        try:
            c.api("PATCH", f"/api/2.0/lakeview/dashboards/{did}", body={"display_name": title, "serialized_dashboard": ser, "warehouse_id": c.warehouse})
        except NotFound:
            did = None
    if not did:
        parent = f"/Workspace/Users/{c.me}/governed-assistant"
        c.cli("workspace", "mkdirs", parent)
        did = c.api("POST", "/api/2.0/lakeview/dashboards", body={"display_name": title, "serialized_dashboard": ser, "warehouse_id": c.warehouse,
                                                                  "parent_path": parent})["dashboard_id"]
        st = "created"
    c.api("POST", f"/api/2.0/lakeview/dashboards/{did}/published", body={"warehouse_id": c.warehouse, "embed_credentials": False})
    c.save(dashboard_id=did, dashboard_url=f"{c.w.config.host}/dashboardsv3/{did}/published")
    log("usage", st, f"dashboard {title} → {c.state['dashboard_url']}")


# ---------- 9. Smoke test ----------
def step_smoke(c):
    import urllib.request
    url = c.state.get("app_url") or (c.cli("apps", "get", c.app) or {}).get("url")
    tok, last = c.w.config.authenticate()["Authorization"], None
    for _ in range(12):
        try:
            with urllib.request.urlopen(urllib.request.Request(url + "/api/config", headers={"Authorization": tok}), timeout=30) as r:
                cfg = json.loads(r.read())
            log("smoke", "ok", f"{url} answers as « {cfg.get('brand')} » for {cfg.get('user')}")
            return
        except Exception as e:  # app still starting
            last = e
            time.sleep(10)
    raise RuntimeError(f"smoke failed on {url}/api/config: {last}")


STEPS = {"uc": step_uc, "policies": step_policies, "lakebase": step_lakebase, "mlflow": step_mlflow, "usage": step_usage,
         "build": step_build, "preflight": step_preflight, "app": step_app, "smoke": step_smoke}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=str(ROOT / "config" / "assistant.yaml"))
    ap.add_argument("--only", help="comma-separated steps: " + ",".join(STEPS))
    ap.add_argument("--dry-run", action="store_true", help="print what would be done, change nothing")
    ap.add_argument("--force", action="store_true", help="adopt existing model services not created by this installer (rewrites their governance)")
    a = ap.parse_args()
    ctx = Ctx(a.config, a.dry_run, a.force)
    todo = a.only.split(",") if a.only else list(STEPS)
    unknown = [s for s in todo if s not in STEPS]
    if unknown:
        raise SystemExit(f"unknown step(s) {unknown}; choose from {', '.join(STEPS)}")
    todo = with_deploy_gate(todo)
    for name in todo:
        t0 = time.time()
        STEPS[name](ctx)
        log(name, "done", f"{time.time() - t0:.0f}s")
    print(json.dumps({k: v for k, v in ctx.state.items() if k in ("services", "experiment_id", "job_id", "dashboard_url", "app_url")}, indent=1))
