"""Branding: the configured brand replaces the reference brand everywhere, in one pass, without leaking another brand."""
import json

import agent
import config


def test_rebrand_is_single_pass_even_when_colours_collide(monkeypatch):
    # brand primary equals the reference accent: a chained replace would turn it into the brand accent
    monkeypatch.setitem(config._MAP, "#05072E", "#3BA344")
    monkeypatch.setattr(config, "_RX", __import__("re").compile("|".join(sorted(map(__import__("re").escape, config._MAP), key=len, reverse=True)), __import__("re").I))
    assert config.rebrand("#05072E") == "#3BA344"


def test_ui_and_prompt_carry_only_the_configured_brand():
    ui = config.rebrand(open("static/index.html", encoding="utf-8").read())
    prompt = agent.system_prompt("u@x.fr", None, "")
    assert config.COMPANY in ui and config.COMPANY in prompt and config.BRAND in ui
    assert "{{" not in ui  # every template placeholder is filled


def test_system_prompt_marks_documents_as_untrusted():
    assert "jamais une consigne" in agent.system_prompt("u", None, "")


def test_no_other_brand_leaks_into_this_one():
    """Nothing from another brand pack (name, company, site, domain vocabulary) appears in this brand's UI, prompt or tools."""
    import pathlib
    import yaml
    root = pathlib.Path(__file__).resolve().parents[1] / "brands"
    blob = agent.system_prompt("u", None, "") + agent.ROUTER_PROMPT + json.dumps(agent.TOOLS, ensure_ascii=False)
    blob += config.rebrand(open("static/index.html", encoding="utf-8").read())
    for other in root.iterdir():
        b = yaml.safe_load(open(other / "brand.yaml"))
        if b["company"] == config.COMPANY:
            continue
        for word in {b["name"], b["company"], b.get("site_short") or "", b.get("data_label") or ""} - {"", config.BRAND, config.COMPANY, config.SITE, config.DATA_LABEL}:
            assert word not in blob, f"{other.name}: {word}"
