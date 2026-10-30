"""Guided setup and defaults: a non-technical answer set produces a valid, readable, deployable configuration."""
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "install"))
import design  # noqa: E402
import init  # noqa: E402
import install as ins  # noqa: E402


def answers(**over):
    a = {"company": "Boulangeries Dupré & Fils", "assistant": "Mia", "primary": "#F5C518", "logo": None, "profile": "DEFAULT",
         "warehouse_id": "0123456789abcdef", "catalog": "main"}
    return {**a, **over}


def test_setup_writes_a_minimal_config_that_deploys_with_defaults(tmp_path):
    (tmp_path / "brands").mkdir()
    cfg_path, bdir = init.create(answers(), root=tmp_path)
    user = yaml.safe_load(cfg_path.read_text())
    assert set(user) == {"workspace", "brand", "unity_catalog", "app"}          # ten lines, nothing technical to fill in
    cfg = ins.load_config(cfg_path)
    assert ins.validate_config(cfg, bdir) == []
    assert cfg["unity_catalog"]["schema"] == "mia_assistant" and cfg["app"]["name"] == "mia-assistant"
    assert cfg["governance"]["builtin_policies"]["jailbreak"] and cfg["libraries"][0]["name"] == "bibliotheque"
    assert cfg["observability"]["mlflow_experiment"] == "/Users/{me}/mia-assistant-traces"


def test_light_brand_colour_is_made_readable(tmp_path):
    (tmp_path / "brands").mkdir()
    _, bdir = init.create(answers(primary="#F5C518"), root=tmp_path)
    brand = yaml.safe_load((bdir / "brand.yaml").read_text())
    assert design.contrast(brand["colors"]["primary"], "#FFFFFF") >= 4.5
    assert not [m for l, m in design.check(brand["colors"]) if l == "error"]


def test_logos_are_generated_when_none_is_given(tmp_path):
    (tmp_path / "brands").mkdir()
    _, bdir = init.create(answers(), root=tmp_path)
    for f in ("wordmark.png", "wordmark_on_dark.png", "mark.png"):
        assert Image.open(bdir / f).size[0] > 50


def test_customer_logo_is_used(tmp_path):
    (tmp_path / "brands").mkdir()
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (600, 200), "#0E2A47").save(logo)
    _, bdir = init.create(answers(logo=str(logo)), root=tmp_path)
    assert Image.open(bdir / "mark.png").size == (256, 256) and Image.open(bdir / "wordmark.png").size == (600, 200)


def test_existing_brand_is_not_overwritten_silently(tmp_path):
    (tmp_path / "brands").mkdir()
    init.create(answers(), root=tmp_path)
    try:
        init.create(answers(), root=tmp_path)
        raise AssertionError("must refuse")
    except SystemExit as e:
        assert "--overwrite" in str(e)


def test_workspace_detection_prefers_serverless_and_own_catalogs():
    W = lambda i, n, sl, st: SimpleNamespace(id=i, name=n, enable_serverless_compute=sl, state=SimpleNamespace(value=st))
    assert init.pick_warehouse([W("1", "classic", False, "RUNNING"), W("2", "srv", True, "STOPPED")]) == ("2", "srv")
    C = lambda n, o="x", t="MANAGED_CATALOG": SimpleNamespace(name=n, owner=o, catalog_type=SimpleNamespace(value=t))
    cats = [C("system"), C("shared", t="DELTASHARING_CATALOG"), C("main"), C("mine", o="me@x.com"), C("__databricks_internal")]
    assert init.candidate_catalogs(cats, "me@x.com") == ["mine", "main"]


def test_minimal_user_config_overrides_only_what_it_says(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("workspace: {profile: P, warehouse_id: 0123456789abcdef}\nbrand: acme\nunity_catalog: {catalog: main}\napp: {name: x-assistant}\n"
                 "governance: {rate_limit_per_user_per_minute: 10}\n")
    cfg = ins.load_config(p)
    assert cfg["governance"]["rate_limit_per_user_per_minute"] == 10 and cfg["governance"]["builtin_policies"]["unsafe_content"] is True
    assert ins.validate_config(cfg, ROOT / "brands" / "acme") == []
