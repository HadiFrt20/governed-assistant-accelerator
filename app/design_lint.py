"""Design-system linter for generated web pages: checked before publishing, so a page that would render empty, broken or
off-brand is sent back to the model with the exact problems instead of reaching the user.

The page contract (see the create_html tool): body_html holds one <section class="em-panel" id="…"> per tab with empty
<div id="…"></div> slots, and the script fills the slots by calling the design-system components EM.*. The list of components
is read from design/ds.js itself, so the linter can never drift from the component library.
"""
import json
import os
import re

_here = os.path.dirname(os.path.abspath(__file__))
_DS = open(os.path.join(_here, "design", "ds.js"), encoding="utf-8").read()
COMPONENTS = set(re.findall(r"\w+", re.findall(r"return \{([^}]*)\};\s*\}\)\(\);", _DS)[-1]))
FORBIDDEN_JS = [(r"\bfetch\s*\(|XMLHttpRequest|WebSocket|EventSource|navigator\.sendBeacon", "appel réseau"),
                (r"\beval\s*\(|new\s+Function\s*\(|\bimport\s*\(", "code dynamique"),
                (r"document\.write|\.innerHTML\s*=|\.outerHTML\s*=|insertAdjacentHTML", "HTML écrit à la main (utiliser les composants EM.*)"),
                (r"\blocation\b|window\.open|localStorage|sessionStorage|document\.cookie", "navigation ou stockage"),
                (r"<\s*/?\s*script", "balise script")]
COLOUR = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|\brgba?\(|\bhsla?\(")


def lint(spec):
    """{"errors": [...], "warnings": [...], "score": 0..100, "components": {...}} for a create_html spec."""
    body, script = spec.get("body_html") or "", spec.get("script") or ""
    data = spec.get("data") if isinstance(spec.get("data"), dict) else {}
    tabs = [t.get("id") for t in spec.get("tabs") or [] if isinstance(t, dict)]
    errors, warnings = [], []

    used = re.findall(r"\bEM\.(\w+)\s*\(", script)
    unknown = sorted(set(used) - COMPONENTS)
    if unknown:
        errors.append(f"composants inconnus : {', '.join('EM.' + u for u in unknown)} ; disponibles : {', '.join('EM.' + c for c in sorted(COMPONENTS))}")
    if not used:
        errors.append("le script n'appelle aucun composant EM.* : la page serait vide")

    ids = set(re.findall(r'\bid\s*=\s*["\']([^"\']+)["\']', body))
    panels = re.findall(r'<section[^>]*class\s*=\s*["\'][^"\']*\bem-panel\b[^"\']*["\'][^>]*\bid\s*=\s*["\']([^"\']+)["\']|'
                        r'<section[^>]*\bid\s*=\s*["\']([^"\']+)["\'][^>]*class\s*=\s*["\'][^"\']*\bem-panel\b', body)
    panels = [a or b for a, b in panels]
    targets = re.findall(r"""\bEM\.\w+\(\s*["']#([\w-]+)["']""", script) + re.findall(r"""getElementById\(\s*["']([\w-]+)["']""", script)
    missing = sorted(set(targets) - ids)
    if missing:
        errors.append(f"le script vise des emplacements absents de body_html : {', '.join('#' + m for m in missing)}")
    if tabs:
        if sorted(set(tabs)) != sorted(set(panels)):
            errors.append(f"onglets {tabs} et sections em-panel {panels} ne correspondent pas (un onglet = une section, même id)")
        for p in panels:
            m = re.search(r'<section[^>]*\bid\s*=\s*["\']' + re.escape(p) + r'["\'][^>]*>(.*?)</section>', body, re.S)
            inner_ids = set(re.findall(r'\bid\s*=\s*["\']([^"\']+)["\']', m.group(1))) if m else set()
            if not inner_ids & set(targets) and not re.search(r"EM\.tab\(\s*[\"']" + re.escape(p), script):
                errors.append(f"l'onglet « {p} » ne contient aucun composant rempli par le script : il s'afficherait vide")
    for pattern, why in FORBIDDEN_JS:
        if re.search(pattern, script, re.I):
            errors.append(f"script interdit ({why})")
    refs = set(re.findall(r"\bDATA\.(\w+)", script))
    absent = sorted(refs - set(data))
    if absent:
        errors.append(f"le script lit des données absentes de data : {', '.join('DATA.' + a for a in absent)}")

    if COLOUR.search(script) or COLOUR.search(re.sub(r'style="width:\s*\d+%"', "", body)):
        warnings.append("couleurs codées en dur : le design system les supprime, utiliser les statuts et composants")
    for p in panels:
        first = re.search(r"""\bEM\.(\w+)\(\s*["']#([\w-]+)["']""", script)
        if first and first.group(1) != "summary" and "summary" not in used:
            warnings.append("aucun EM.summary : chaque onglet commence par le message clé (EM.summary)")
            break
    if len(json.dumps(data, ensure_ascii=False)) > 400_000:
        warnings.append("données de plus de 400 ko : la page sera lente à ouvrir")
    score = max(0, 100 - 25 * len(errors) - 5 * len(warnings))
    return {"errors": errors, "warnings": warnings, "score": score, "components": {c: used.count(c) for c in sorted(set(used))}}
