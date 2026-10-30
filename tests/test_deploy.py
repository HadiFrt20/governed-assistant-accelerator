"""Bundle deployment: config validation, rendered bundle, plan safety, guardrail enforcement and failure handling (no network)."""
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "install"))
import install as ins  # noqa: E402


def base_cfg(**over):
    cfg = yaml.safe_load(open(ROOT / "config/assistant.yaml", encoding="utf-8"))
    cfg["workspace"]["warehouse_id"] = "0123456789abcdef"
    for k, v in over.items():
        cfg[k] = v
    return cfg


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    """A real Ctx on a temp repo root (brands, app and bundle linked to the real ones); the workspace is never reached."""
    for d in ("brands", "app", "bundle", "tests", "requirements-dev.txt"):
        (tmp_path / d).symlink_to(ROOT / d)
    monkeypatch.setattr(ins, "ROOT", tmp_path)
    def make(cfg=None):
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml.safe_dump(cfg or base_cfg(), allow_unicode=True), encoding="utf-8")
        c = ins.Ctx(str(path), SimpleNamespace(plan=False, allow_destructive=False, yes=False))
        c.phase, c.me, c.workspace_id, c.host = "test", "deployer@example.com", "42", "https://ws.example"
        c._w = SimpleNamespace()  # any workspace call fails loudly
        return c
    return make


# ---------- configuration ----------
def test_shipped_config_is_valid_once_a_warehouse_is_set():
    assert ins.validate_config(base_cfg(), ROOT / "brands" / "acme") == []


def test_placeholder_warehouse_and_bad_names_are_all_reported():
    cfg = base_cfg()
    cfg["workspace"]["warehouse_id"] = "<warehouse-id>"
    cfg["app"]["name"] = "Acme_Assistant"
    cfg["unity_catalog"]["schema"] = "acme-assistant"
    keys = {k for k, _, _ in ins.validate_config(cfg, ROOT / "brands" / "acme")}
    assert {"workspace.warehouse_id", "app.name", "unity_catalog.schema"} <= keys


def test_unknown_sensitive_category_is_refused_before_anything_is_deployed():
    cfg = base_cfg()
    cfg["governance"]["builtin_policies"]["sensitive_data"]["categories"] = ["class.person"]
    problems = ins.validate_config(cfg, ROOT / "brands" / "acme")
    assert any("ALL traffic" in p for _, p, _ in problems)
    with pytest.raises(ins.DeployError):
        ins.desired_policies(cfg, "c.s")


def test_ask_is_refused_on_model_services():
    cfg = base_cfg()
    cfg["governance"]["builtin_policies"]["sensitive_data"]["action"] = "ask"
    with pytest.raises(ins.DeployError, match="action"):
        ins.desired_policies(cfg, "c.s")


def test_config_error_exits_2_with_the_fix(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    cfg = base_cfg()
    cfg["workspace"]["warehouse_id"] = "<warehouse-id>"
    bad.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    assert ins.main(["--config", str(bad)]) == 2
    err = capsys.readouterr().err
    assert "workspace.warehouse_id" in err and "16 hex" in err


# ---------- bundle definition ----------
def _bundle_docs():
    files = [ROOT / "bundle" / "databricks.yml"] + sorted((ROOT / "bundle" / "resources").glob("*.yml"))
    return {f.name: (f.read_text(encoding="utf-8"), yaml.safe_load(f.read_text(encoding="utf-8"))) for f in files}


def test_every_variable_used_is_declared_and_rendered():
    docs = _bundle_docs()
    declared = set()
    for _, d in docs.values():
        declared |= set((d or {}).get("variables", {}))
    used = set()
    for text, _ in docs.values():
        used |= set(re.findall(r"\$\{var\.([a-z_]+)\}", text))
    assert used <= declared, used - declared
    rendered = ins.bundle_variables(base_cfg(), {"name": "Atlas"})
    assert set(rendered) == declared  # developer configured in the shipped config: every variable gets a value


def test_without_developer_role_no_developer_variables():
    cfg = base_cfg()
    cfg["models"]["developer"] = None
    v = ins.bundle_variables(cfg, {"name": "Atlas"})
    assert "developer_service" not in v and v["lakebase_catalog"] == "acme_assistant_lb"


def test_obo_off_means_no_user_scopes():
    cfg = base_cfg()
    cfg["app"]["obo"] = False
    assert ins.bundle_variables(cfg, {"name": "A"})["user_api_scopes"] == []
    assert ins.bundle_variables(base_cfg(), {"name": "A"})["user_api_scopes"] == ins.APP_SCOPES


def test_app_identity_grants_are_least_privilege():
    """The app's service principal: usage on its schema, read/write on its volume, write on the trace schema only."""
    allowed = {"assistant_schema": {"USE_SCHEMA"}, "traces_schema": {"USE_SCHEMA", "SELECT", "MODIFY"}, "files": {"READ_VOLUME", "WRITE_VOLUME"},
               "assistant_service": {"EXECUTE"}}
    seen = {}
    for _, d in _bundle_docs().values():
        for kind, items in ((d or {}).get("resources") or {}).items():
            for key, r in items.items():
                for g in r.get("grants", []) if isinstance(r, dict) else []:
                    if "service_principal_client_id" in g["principal"]:
                        seen[key] = set(g["privileges"])
                    assert "ALL_PRIVILEGES" not in g["privileges"] and "MANAGE" not in g["privileges"]
    assert seen == allowed


def test_stateful_resources_are_never_recreated_silently():
    actions = ins.parse_plan("update model_services.assistant_service\nrecreate postgres_projects.history_project\ndelete jobs.usage_job\n\nPlan: 0 to add")
    assert actions == [("update", "model_services.assistant_service"), ("recreate", "postgres_projects.history_project"), ("delete", "jobs.usage_job")]
    assert ins.destructive(actions) == ["recreate postgres_projects.history_project"]  # a job can be recreated, the history cannot


def test_cli_version_and_error_extraction():
    assert ins.cli_version("Databricks CLI v1.20.0") == (1, 20, 0) >= ins.MIN_CLI
    assert ins.cli_version("Databricks CLI v0.250.0") < ins.MIN_CLI
    p = SimpleNamespace(stdout="Uploading...\n", stderr="Error: cannot create apps.assistant: quota\n\nEndpoint: POST x\nAPI message: limit reached\n")
    assert ins.cli_error(p) == "Error: cannot create apps.assistant: quota | API message: limit reached"


# ---------- rendered bundle ----------
def test_rendered_bundle_is_complete_and_consistent(ctx):
    c = ctx()
    c.build_dir.parent.mkdir(exist_ok=True)
    out = c.build_dir.with_name("render")
    out.mkdir()
    ins.render_bundle(c, out)
    assert (out / "databricks.yml").exists() and (out / "resources" / "developer_service.yml").exists()
    s = json.loads((out / "app" / "settings.json").read_text())
    assert s["security"]["library_curators"] == ["deployer@example.com"] and "experiment_id" not in s["observability"]
    assert s["observability"]["uc_trace_prefix"] == f"{c.catalog}.{c.traces}.assistant"
    assert all((out / "app" / p).exists() for p in s["templates"].values())
    assert not (out / "app" / "app.yaml").exists()  # command and env come from the bundle, not from a stale app.yaml
    sql = (out / "sql" / "gold_usage.sql").read_text()
    assert f"{c.fq}.ref_deployment" in sql and "{" not in sql.replace("{}", "") and "'42'" in sql
    json.loads((out / "dashboards" / "usage.lvdash.json").read_text())
    v = json.loads((out / ".databricks" / "bundle" / "default" / "variable-overrides.json").read_text())
    assert v["app_name"] == c.app and v["users_group"] == "account users"


def test_rendered_bundle_skips_developer_when_not_configured(ctx):
    cfg = base_cfg()
    cfg["models"]["developer"] = None
    c = ctx(cfg)
    out = c.build_dir.with_name("render")
    out.mkdir(parents=True)
    ins.render_bundle(c, out)
    assert not (out / "resources" / "developer_service.yml").exists() and "__none__" in (out / "sql" / "gold_usage.sql").read_text()


def test_build_is_atomic_and_keeps_bundle_state(ctx, monkeypatch):
    c = ctx()
    st = c.build_dir / ".databricks" / "bundle" / "default"
    st.mkdir(parents=True)
    (st / "resources.json").write_text("{\"state\": 1}")
    (c.build_dir / "old.txt").write_text("v1")
    monkeypatch.setattr(ins, "render_bundle", lambda c, out: (_ for _ in ()).throw(RuntimeError("template error")))
    with pytest.raises(RuntimeError):
        ins.phase_build(c)
    assert (c.build_dir / "old.txt").read_text() == "v1" and not list(c.build_dir.parent.glob(f".{c.app}.*"))

    def ok(c, out):
        (out / ".databricks" / "bundle" / "default").mkdir(parents=True)
        (out / "new.txt").write_text("v2")
        return {"pptx": "x"}
    monkeypatch.setattr(ins, "render_bundle", ok)
    monkeypatch.setattr(ins, "test_gate", lambda c: None)
    ins.phase_build(c)
    assert (c.build_dir / "new.txt").read_text() == "v2" and not (c.build_dir / "old.txt").exists()
    assert (c.build_dir / ".databricks" / "bundle" / "default" / "resources.json").read_text() == "{\"state\": 1}"


def test_failing_test_suite_stops_the_deployment(ctx, monkeypatch):
    c = ctx()
    (c.build_dir / "app").mkdir(parents=True)
    monkeypatch.setattr(ins.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="3 failed, 120 passed", stderr=""))
    with pytest.raises(ins.DeployError, match="3 failed"):
        ins.test_gate(c)


# ---------- deploy safety ----------
def test_plan_that_drops_history_is_refused(ctx, monkeypatch):
    c = ctx()
    runs = iter([SimpleNamespace(returncode=0, stdout="", stderr=""),                                      # validate
                 SimpleNamespace(returncode=0, stdout="recreate postgres_projects.history_project\n", stderr="")])  # plan
    monkeypatch.setattr(c, "bundle", lambda *a, **k: next(runs))
    monkeypatch.setattr(ins, "bundle_summary", lambda c: {})
    monkeypatch.setattr(ins, "find_conflicts", lambda c, r: [])
    with pytest.raises(ins.DeployError, match="hold data"):
        ins.phase_deploy(c)


def test_existing_objects_stop_the_deploy_with_bind_commands(ctx, monkeypatch):
    c = ctx()
    monkeypatch.setattr(c, "bundle", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(ins, "bundle_summary", lambda c: {})
    monkeypatch.setattr(ins, "find_conflicts", lambda c, r: [("schemas", "assistant_schema", c.fq)])
    with pytest.raises(ins.DeployError) as e:
        ins.phase_deploy(c)
    assert f"bind assistant_schema '{c.fq}'" in e.value.fix


def test_conflicts_ignore_resources_already_in_the_bundle(ctx):
    from databricks.sdk.errors import NotFound
    c = ctx()
    def missing(*a, **k):
        raise NotFound("nope")
    c._w = SimpleNamespace(schemas=SimpleNamespace(get=lambda name: {"name": name}), apps=SimpleNamespace(get=missing),
                           catalogs=SimpleNamespace(get=missing), experiments=SimpleNamespace(get_by_name=missing),
                           api_client=SimpleNamespace(do=missing))
    found = ins.find_conflicts(c, {"schemas": {"assistant_schema": {"id": c.fq}}})
    assert found == [("schemas", "traces_schema", f"{c.catalog}.{c.traces}")]


# ---------- guardrails ----------
def test_policies_match_ignores_deleted_entries_and_detects_changes():
    pols = ins.desired_policies(base_cfg(), "c.s")
    assert ins.policies_match({"service_policies": pols + [{"name": "old", "is_deleted": True}]}, pols)
    assert not ins.policies_match({"service_policies": pols[:-1]}, pols)
    changed = json.loads(json.dumps(pols))
    changed[-1]["options"]["action"] = "block"
    assert not ins.policies_match({"service_policies": changed}, pols)
    assert not ins.policies_match({}, pols)  # a bundle update clears the policies: detected


def _govern_ctx(ctx, read_back):
    c = ctx()
    calls = []
    c.deployed = {"app_sp": "sp-1", "app_url": "https://app", "dashboard_id": "d1", "services": {}}
    c._w = SimpleNamespace(files=SimpleNamespace(create_directory=lambda p: calls.append(("mkdir", p))),
                           api_client=SimpleNamespace(do=lambda *a, **k: {}))
    c.sql = lambda stmt, fetch=False: calls.append(("sql", stmt[:200])) or []
    c.cli = lambda *a, **k: calls.append(("cli",) + a) or {}
    def api(method, path, body=None, query=None):
        calls.append((method, path))
        return read_back(method) if method == "GET" else {}
    c.api = api
    return c, calls


def test_policies_that_do_not_stick_close_the_services(ctx):
    c, calls = _govern_ctx(ctx, lambda m: {"config": {"service_policies": []}})
    with pytest.raises(ins.DeployError, match="guardrail policies"):
        ins.phase_govern(c)
    removed = [json.loads(a[-1])["changes"][0] for a in calls if a[0] == "cli" and "remove" in a[-1]]
    assert {"principal": "account users", "remove": ["EXECUTE"]} in removed and {"principal": "sp-1", "remove": ["EXECUTE"]} in removed
    assert any(x[0] == "PATCH" for x in calls)


def test_govern_applies_policies_and_reference_data(ctx):
    c = ctx()
    pols = ins.desired_policies(c.cfg, c.fq)
    good = {"config": {"service_policies": pols, "routing": {"destinations": [{"pay_per_token_config": {"model": "models/system.ai.databricks-claude-sonnet-5-5"}}]},
                       "rate_limits": [{"requests": 60}], "inference_table": {"table": "tables/c.s.t"}}}
    c, calls = _govern_ctx(ctx, lambda m: good)
    ins.phase_govern(c)
    assert not [x for x in calls if x[0] == "PATCH"]  # already compliant: no rewrite
    sql = [x[1] for x in calls if x[0] == "sql"]
    assert any("ref_deployment" in s for s in sql) and any("ref_guardrail_config" in s for s in sql)
    assert ("mkdir", f"{c.files_root}/uploads") in calls
    assert c.deployed["dashboard_url"].endswith("/dashboardsv3/d1/published")


def test_gold_sql_reads_the_app_identity_from_reference_data():
    sql = (ROOT / "install" / "sql" / "gold_usage.sql").read_text()
    assert "{sp}" not in sql and "(SELECT app_service_principal FROM {fq}.ref_deployment)" in sql
