"""Design systems: import from DTCG / Figma / CSS, accessibility checks, application to the brand, and the page linter."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "install"))
import design  # noqa: E402

import agent  # noqa: E402
import design_lint  # noqa: E402
import security  # noqa: E402

DTCG = {"color": {"$type": "color", "brand": {"900": {"$value": "#0B2545"}, "primary": {"$value": "{color.brand.900}"}},
                  "accent": {"$value": "#13A89E"}, "secondary": {"$value": "#1D4ED8"}},
        "font": {"family": {"heading": {"$type": "fontFamily", "$value": ["Poppins", "sans-serif"]},
                            "body": {"$type": "fontFamily", "$value": "Source Sans 3"}}}}
FIGMA = {"variables": {"1": {"name": "Color/Primary", "resolvedType": "COLOR", "valuesByMode": {"m": {"r": 0.05, "g": 0.2, "b": 0.35, "a": 1}}},
                       "2": {"name": "Color/Accent", "resolvedType": "COLOR", "valuesByMode": {"m": {"r": 0.9, "g": 0.4, "b": 0.1, "a": 1}}}}}
CSS = ":root { --brand-primary: #1F3A5F; --brand-dark: #142740; --color-accent: var(--orange); --orange: #E07A1F; --font-body: 'Lato', sans-serif; }"


def _file(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return p


def test_dtcg_tokens_with_aliases_and_font_stacks(tmp_path):
    roles = design.map_roles(design.read_tokens(_file(tmp_path, "tokens.json", DTCG)))
    assert roles["primary"][1] == "#0B2545" and roles["accent"][1] == "#13A89E" and roles["secondary"][1] == "#1D4ED8"
    assert roles["heading"][1] == "Poppins" and roles["body"][1] == "Source Sans 3"
    assert roles["primary_2"][1] != roles["primary"][1]          # derived darker shade when the system has none


def test_figma_variables_export(tmp_path):
    roles = design.map_roles(design.read_tokens(_file(tmp_path, "figma.json", FIGMA)))
    assert roles["primary"][1] == "#0D3359" and roles["accent"][1] == "#E6661A"


def test_css_custom_properties_resolve_var_references(tmp_path):
    roles = design.map_roles(design.read_tokens(_file(tmp_path, "styles.css", CSS)))
    assert roles["primary"][1] == "#1F3A5F" and roles["primary_2"][1] == "#142740" and roles["accent"][1] == "#E07A1F" and roles["body"][1] == "Lato"


def test_explicit_mapping_wins_and_unknown_token_is_refused(tmp_path):
    flat = design.read_tokens(_file(tmp_path, "tokens.json", DTCG))
    assert design.map_roles(flat, {"primary": "color.secondary"})["primary"][1] == "#1D4ED8"
    with pytest.raises(SystemExit):
        design.map_roles(flat, {"primary": "color.nope"})


def test_unreadable_primary_is_an_error_light_accent_a_warning():
    issues = design.check({"primary": "#FFD400", "primary_2": "#0B2545", "accent": "#F5F5F5"})
    assert [l for l, _ in issues].count("error") >= 1 and any(l == "warning" and "accent" in m for l, m in issues)
    assert not [m for l, m in design.check({"primary": "#0B2545", "primary_2": "#06162B", "accent": "#0F766E", "secondary": "#1D4ED8"}) if l == "error"]
    assert design.contrast("#000000", "#FFFFFF") == pytest.approx(21.0)


def test_import_writes_tokens_that_override_the_brand(tmp_path, monkeypatch):
    bdir = tmp_path / "brands" / "x"
    bdir.mkdir(parents=True)
    (bdir / "brand.yaml").write_text("name: X\ncolors: {primary: '#111111', primary_2: '#222222', accent: '#333333', secondary: '#444444'}\nfonts: {body: Inter}\n")
    monkeypatch.setattr(design, "ROOT", tmp_path)
    design.main(["import", "--brand", "x", str(_file(tmp_path, "tokens.json", DTCG))])
    import yaml
    b = design.apply(yaml.safe_load((bdir / "brand.yaml").read_text()), bdir)
    assert b["colors"]["primary"] == "#0B2545" and b["fonts"]["body"] == "Source Sans 3" and b["fonts"]["heading"] == "Poppins"
    assert "tokens.json" in b["design_source"]


def test_import_refuses_unreadable_colours(tmp_path, monkeypatch):
    bdir = tmp_path / "brands" / "x"
    bdir.mkdir(parents=True)
    (bdir / "brand.yaml").write_text("name: X\ncolors: {primary: '#111111'}\n")
    monkeypatch.setattr(design, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="illisibles"):
        design.main(["import", "--brand", "x", str(_file(tmp_path, "t.css", ":root { --primary: #FFE066; }"))])
    assert not (bdir / "design" / "tokens.json").exists()


# ---------- page linter ----------
GOOD = {"title": "Volumes", "tabs": [{"id": "a", "label": "Synthèse"}, {"id": "b", "label": "Détail"}],
        "body_html": '<section class="em-panel" id="a"><div id="s"></div><div id="k"></div></section><section class="em-panel" id="b"><div id="t"></div></section>',
        "data": {"sites": [{"site": "Lyon", "v": 1}]},
        "script": "EM.summary('#s', {title: 'x', points: []}); EM.kpis('#k', [{label: 'Volume', value: DATA.sites.length}]);"
                  "EM.table('#t', {columns: [{key: 'site', label: 'Site'}], rows: DATA.sites});"}


def test_components_come_from_the_design_library():
    assert {"summary", "kpis", "table", "capabilityMap", "cards"} <= design_lint.COMPONENTS


def test_well_formed_page_passes():
    r = design_lint.lint(GOOD)
    assert r["errors"] == [] and r["score"] >= 95 and r["components"]["table"] == 1


@pytest.mark.parametrize("change,expect", [
    ({"script": GOOD["script"] + "EM.chart('#s', {});"}, "composants inconnus"),
    ({"script": GOOD["script"].replace("'#t'", "'#missing'")}, "emplacements absents"),
    ({"tabs": [{"id": "a", "label": "A"}]}, "ne correspondent pas"),
    ({"script": "EM.summary('#s', {}); EM.kpis('#k', []);"}, "s'afficherait vide"),
    ({"script": GOOD["script"] + "fetch('https://x');"}, "appel réseau"),
    ({"script": GOOD["script"] + "document.getElementById('s').innerHTML = '<b>x</b>';"}, "HTML écrit à la main"),
    ({"script": GOOD["script"] + "EM.callout('#s', DATA.notes);"}, "DATA.notes"),
    ({"script": ""}, "aucun composant"),
])
def test_broken_pages_are_caught(change, expect):
    assert any(expect in e for e in design_lint.lint({**GOOD, **change})["errors"])


def test_hard_coded_colours_are_a_warning():
    r = design_lint.lint({**GOOD, "script": GOOD["script"] + "const c = '#FF0000';"})
    assert not r["errors"] and any("couleurs" in w for w in r["warnings"])


def test_agent_sends_a_broken_page_back_once_then_publishes(monkeypatch):
    from test_agent import FakeGateway, text, tool_use
    monkeypatch.setattr(agent, "upload", lambda token, name, data: f"/Volumes/c/s/v/generated/{name}")
    bad = {**GOOD, "script": "EM.summary('#s', {});"}
    j = {"events": [], "files": [], "answer": None, "messages": [{"role": "user", "content": "page"}]}
    gw = FakeGateway(j, [tool_use("create_html", bad, "t1"), tool_use("create_html", GOOD, "t2"), text("Publiée.")])
    agent.agent_loop(gw, j, "alice@acme.com", None, "", security.user_folder("alice@acme.com"))
    first = gw.sent[1][-1]["content"][0]
    assert first.get("is_error") and "design system" in first["content"] and len(j["files"]) == 1
