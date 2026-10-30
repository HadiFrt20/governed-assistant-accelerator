"""Deployment settings and brand, read from settings.json (written by install/install.py). Nothing customer-specific lives in code."""
import json
import os
import re

_here = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.environ.get("SETTINGS_FILE", "settings.json")
S = json.load(open(SETTINGS_FILE if os.path.isabs(SETTINGS_FILE) else os.path.join(_here, SETTINGS_FILE), encoding="utf-8"))

B = S["brand"]
BRAND = B["name"]                          # e.g. "Atlas"
COMPANY = B["company"]                     # e.g. "Acme Industries"
SITE = B.get("site", "")
SITE_SHORT = B.get("site_short") or SITE
ORG = B.get("org_label", "")
DESCRIPTION = B.get("description", f"l'assistant interne de {COMPANY}")
INSTRUCTIONS = B.get("instructions", "").strip()        # domain rules (e.g. how to write an 8D), appended to the system prompt
DATA_LABEL = B.get("data_label", "données de l'entreprise")
DATA_EXAMPLES = B.get("data_examples", "indicateurs, volumes, délais, non-conformités, tendances, comparaisons entre équipes ou sites")   # how users name their data (e.g. "données de l'usine")
FOOTER = B.get("footer", f"Vos droits d'accès s'appliquent · données et documents restent chez {COMPANY}")
COLORS = B["colors"]
FONTS = B.get("fonts", {})
LOGOS = B["logos"]                         # URL paths under /brand/
APP_ID = re.sub(r"[^a-z0-9]+", "-", BRAND.lower()).strip("-")

UC = S["uc"]
FILES_VOLUME, GENERATED, UPLOADS = UC["files_root"], UC["generated"], UC["uploads"]
# document libraries: one UC volume each, read with the user's identity (Unity Catalog decides who sees which library)
LIBRARIES = UC.get("libraries") or [{"id": "bibliotheque", "label": "Bibliothèque", "root": UC["library"]}]
LIBRARY = LIBRARIES[0]["root"]            # default library (uploads by curators go here unless another is chosen)
LIBRARY_ROOTS = tuple(l["root"] for l in LIBRARIES)
WAREHOUSE_ID = os.environ.get("MLFLOW_TRACING_SQL_WAREHOUSE_ID") or (S.get("observability") or {}).get("warehouse_id", "")

MODEL = S["models"]["assistant"]
MAX_OUTPUT_TOKENS = int(S["models"].get("max_output_tokens", 64000))
EFFORT = S["models"].get("effort", "medium")

GENIE_ONE = bool(S["data"].get("genie_one", True))
DATA_SCOPE = S["data"].get("scope", "les données métier gouvernées par Unity Catalog")

POLICIES = S["governance"]["policies"]                    # {name: one-line description}, shown in traces
ANON_LABELS = set(S["governance"].get("anonymised_labels", ["adresse email", "IBAN", "carte bancaire"]))

PRICES = {k: tuple(v) for k, v in S["pricing"].items()}
TEMPLATE_FILES = S.get("templates", {"pptx": "templates/deck.pptx", "docx_8d": "templates/8d.docx"})

# UI entry points: templates (cards on the home page) and per-department suggestions
TEMPLATES = B.get("templates", [])
DEPARTMENTS = B.get("suggestions") or {d: [] for d in B.get("departments", [])}


# ---------- branding of shared assets ----------
# UI, design system and Office renderers are written against a reference palette; the brand swaps it at serve time.
REFERENCE = {"#05072E": "primary", "#1D1F46": "primary_2", "#3BA344": "accent", "#2EA3F2": "secondary"}


def _mix(hex_color, other, t):
    a = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(a, b))


def _mapping():
    """Reference brand value -> configured brand value, for colours (and derived shades), fonts, logos and names."""
    heading, body = FONTS.get("heading", "Titillium Web"), FONTS.get("body", FONTS.get("heading", "Titillium Web"))
    m = {ref: COLORS[key] for ref, key in REFERENCE.items()}
    m.update({"#2E8B36": _mix(COLORS["accent"], "#000000", .2), "#EAF6EB": _mix(COLORS["accent"], "#FFFFFF", .9),
              "#CFE8D2": _mix(COLORS["accent"], "#FFFFFF", .75), "#F3FAF4": _mix(COLORS["accent"], "#FFFFFF", .94),
              "#1E6B2A": _mix(COLORS["accent"], "#000000", .35)})
    m.update({"Titillium Web": heading, "Titillium+Web": heading.replace(" ", "+"), "Open Sans": body, "Open+Sans": body.replace(" ", "+")})
    # placeholders used by the UI template (static/index.html)
    m.update({"{{BRAND}}": BRAND, "{{COMPANY}}": COMPANY, "{{SITE}}": SITE, "{{SITE_SHORT}}": SITE_SHORT, "{{DATA_LABEL}}": DATA_LABEL,
              "{{FOOTER}}": FOOTER, "{{ORG}}": ORG or "", "{{WORDMARK}}": LOGOS["wordmark"].split("/")[-1],
              "{{MARK}}": LOGOS["mark"].split("/")[-1], "{{FAVICON}}": LOGOS.get("favicon", LOGOS["mark"]).split("/")[-1]})
    return m


_MAP = _mapping()
_RX = re.compile("|".join(re.escape(k) for k in sorted(_MAP, key=len, reverse=True)), re.I)


def rebrand(text):
    """Apply brand colours, fonts, logos and names to a UI/CSS/HTML string written against the reference brand.
    Single pass: a replaced value is never re-mapped (safe when a brand colour equals a reference colour)."""
    lower = {k.lower(): v for k, v in _MAP.items()}
    return _RX.sub(lambda m: _MAP.get(m.group(0), lower.get(m.group(0).lower(), m.group(0))), text)


def hex_rgb(key):
    h = COLORS[key].lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
