"""settings.json for the app, computed from config/assistant.yaml + brand.yaml + the generated Office templates. Pure function:
no workspace calls, so tests and local builds can import it. Deployed ids (experiment, Lakebase host) are not in here: the
bundle passes them to the app as environment variables (resources/app.yml)."""

# Categories accepted by system.ai.detect_sensitive_data. An unknown value makes the policy unevaluable and the
# gateway then fails closed on ALL traffic, so the installer refuses anything outside this list.
SENSITIVE_CATEGORIES = {
    "class.email_address": "adresse email", "class.ip_address": "adresse IP", "class.mac_address": "adresse MAC", "class.vin": "numéro VIN",
    "class.credit_card": "carte bancaire", "class.iban_code": "IBAN", "class.phone_number": "téléphone", "class.us_ssn": "numéro SSN (US)",
    "class.us_itin": "ITIN (US)", "class.us_passport": "passeport (US)", "class.us_bank_number": "compte bancaire (US)", "class.uk_nhs": "NHS (UK)",
    "class.uk_nino": "NINO (UK)", "class.in_pan": "PAN (Inde)", "class.in_aadhaar": "Aadhaar (Inde)"}
# the built-in detector does not recognise every national phone format (French mobiles were not masked in tests): never claim it in the UI
NOT_CLAIMED = {"class.phone_number"}
TEMPLATE_KEYS = ("pptx", "docx_8d", "wordmark", "wordmark_on_dark")


def traces_schema(cfg):
    uc = cfg["unity_catalog"]
    return uc.get("traces_schema") or f"{uc['schema']}_traces"


def libraries(cfg):
    """Document libraries: one UC volume each in the assistant schema. Defaults to a single « Bibliothèque »."""
    uc = cfg["unity_catalog"]
    libs = cfg.get("libraries") or [{"name": "bibliotheque", "label": "Bibliothèque"}]
    return [{"id": l["name"], "label": l.get("label") or l["name"].replace("_", " ").capitalize(),
             "root": f"/Volumes/{uc['catalog']}/{uc['schema']}/{l['name']}", "readers": l.get("readers"), "curators": l.get("curators")} for l in libs]


def policy_descriptions(cfg):
    g = cfg["governance"]
    b = g.get("builtin_policies", {})
    sd = b.get("sensitive_data") or {}
    pols = {g["ip_leak_policy"]["name"]: "DENY (fonction UC, mots-clés propriété intellectuelle)"}
    if b.get("jailbreak", True):
        pols["anti-jailbreak"] = "DENY"
    if b.get("unsafe_content", True):
        pols["contenu-inapproprie"] = "DENY"
    if sd:
        action = sd.get("action", "transform")
        pols["donnees-sensibles"] = ("ANONYMISATION (redact) " if action == "transform" else action.upper() + " ") + \
            ", ".join(SENSITIVE_CATEGORIES[x] for x in sd.get("categories", []))
    return pols


def build(cfg, brand, templates, me=None):
    uc = cfg["unity_catalog"]
    catalog, schema, volume = uc["catalog"], uc["schema"], uc["volume"]
    fq, root = f"{catalog}.{schema}", f"/Volumes/{catalog}/{schema}/{volume}"
    sd = ((cfg["governance"].get("builtin_policies") or {}).get("sensitive_data")) or {}
    sec = cfg.get("security") or {}
    tpl = templates or {}
    missing = [k for k in TEMPLATE_KEYS if k not in tpl]
    if missing:
        raise ValueError(f"Office templates missing from the build ({missing}): run the build step (install/office_templates.py)")
    b = dict(brand)
    b["logos"] = {k: f"/brand/{v}" for k, v in brand["logos"].items()}
    svc = lambda role: f"{fq}.{cfg['models'][role]['service']}" if cfg["models"].get(role) else None
    return {
        "brand": b,
        "uc": {"catalog": catalog, "schema": schema, "volume": volume, "files_root": root,
               "libraries": [{k: l[k] for k in ("id", "label", "root")} for l in libraries(cfg)],
               "generated": f"{root}/generated", "uploads": f"{root}/uploads"},
        "documents": {"parser": (cfg.get("documents") or {}).get("parser", "auto")},
        "models": {"assistant": svc("assistant"), "developer": svc("developer"),
                   "max_output_tokens": cfg["models"].get("max_output_tokens", 64000), "effort": cfg["models"].get("effort", "medium")},
        "data": {"genie_one": cfg["data"].get("genie_one", True), "scope": cfg["data"].get("scope", "")},
        "governance": {"policies": policy_descriptions(cfg), "sensitive_categories": sd.get("categories", []),
                       "anonymised_labels": [SENSITIVE_CATEGORIES[x] for x in sd.get("categories", []) if x not in NOT_CLAIMED]},
        "pricing": cfg.get("pricing", {"default": [2.0, 10.0]}),
        "observability": {"uc_trace_prefix": f"{catalog}.{traces_schema(cfg)}.assistant", "warehouse_id": cfg["workspace"]["warehouse_id"]},
        "obo": bool(cfg["app"].get("obo", True)),
        "templates": {k: tpl[k] for k in TEMPLATE_KEYS},
        "security": {"obo_fallback_to_app": bool(sec.get("obo_fallback_to_app", False)),
                     "library_curators": sec.get("library_curators") or ([me] if me else []),
                     "max_prompt_chars": int(sec.get("max_prompt_chars", 20000)), "max_upload_mb": int(sec.get("max_upload_mb", 25)),
                     "max_running_turns_per_user": int(sec.get("max_running_turns_per_user", 2)),
                     "trace_content": sec.get("trace_content", "redacted")},
    }
