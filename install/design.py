"""Design system of a brand: import it from what the customer already has, check it, apply it.

    python install/design.py import --brand <brand> <tokens.json | figma-variables.json | styles.css> [--map primary=color.brand.900 ...]
    python install/design.py check --brand <brand>

Accepted sources (whatever the design team can export, no tool to install):
  - W3C design tokens (DTCG, `$value`/`$type`, the format of Tokens Studio, Style Dictionary, Figma's native variables export)
  - Figma variables REST export (`variables` / `resolvedValuesByMode`)
  - a CSS file with custom properties (`--brand-primary: #123456;`)
The import writes brands/<brand>/design/tokens.json. At build time the tokens override the colours and fonts of brand.yaml,
for the chat UI, the generated web pages, PowerPoint and Word alike. Nothing else changes: the component library stays the
same, so a new design system never needs code.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROLES = ("primary", "primary_2", "accent", "secondary")   # what the UI and the documents are styled with
FONT_ROLES = ("heading", "body")
HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# name hints per role, most specific first (matched on the token path, lower case, separators normalised to ".")
HINTS = {
    "primary": [r"\bbrand\.primary$", r"\bprimary\.(default|base|main|500|600|700)$", r"\bprimary$", r"\bbrand\.(default|main|base)$", r"\bbrand$"],
    "primary_2": [r"\bprimary\.(dark|strong|hover|800|900)$", r"\bbrand\.(dark|secondary)$", r"\bprimary[._-]?2$", r"\bdark$"],
    "accent": [r"\baccent\.(default|base|main|500)$", r"\baccent$", r"\bhighlight$", r"\bsuccess$"],
    "secondary": [r"\bsecondary\.(default|base|main|500)$", r"\bsecondary$", r"\binfo$", r"\blink$"],
    "heading": [r"font.*(heading|display|title)", r"(heading|display|title).*font"],
    "body": [r"font.*(body|text|base|sans)", r"(body|text).*font", r"font.*family"],
}


# ---------------------------------------------------------------- reading
def _norm(name):
    return re.sub(r"[/\s_-]+", ".", name.strip().lower()).strip(".")


def _hex(v):
    """#rgb / #rrggbb / rgb(a)() / Figma {r,g,b} (0..1) → #RRGGBB, else None."""
    if isinstance(v, dict) and {"r", "g", "b"} <= set(v):
        return "#" + "".join(f"{round(float(v[k]) * 255):02X}" for k in "rgb")
    if isinstance(v, dict) and "hex" in v:  # DTCG 2025 colour object
        v = v["hex"]
    if not isinstance(v, str):
        return None
    v = v.strip()
    if HEX.match(v):
        v = v.upper()
        return "#" + "".join(c * 2 for c in v[1:]) if len(v) == 4 else v
    m = re.match(r"rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)", v)
    return "#" + "".join(f"{int(x):02X}" for x in m.groups()) if m else None


def _font(v):
    if isinstance(v, list):
        v = v[0] if v else ""
    if isinstance(v, dict):
        v = v.get("fontFamily") or v.get("family") or ""
    return str(v).split(",")[0].strip().strip("'\"") or None


def _resolve(flat, v, depth=0):
    """DTCG aliases: {color.brand.500} → its value."""
    while isinstance(v, str) and v.startswith("{") and v.endswith("}") and depth < 10:
        v = flat.get(_norm(v[1:-1]), {}).get("value")
        depth += 1
    return v


def read_tokens(path):
    """Flat {normalised.name: {"value", "type"}} from DTCG JSON, a Figma variables export or a CSS file."""
    text = Path(path).read_text(encoding="utf-8")
    flat = {}
    if Path(path).suffix.lower() == ".css" or text.lstrip().startswith((":root", "--", "/*", "@")):
        for name, value in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", text):
            flat[_norm(name)] = {"value": value.strip(), "type": None}
        for name, value in list(flat.items()):  # var(--x) references
            m = re.match(r"var\(--([\w-]+)\)", value["value"])
            if m and _norm(m.group(1)) in flat:
                flat[name] = flat[_norm(m.group(1))]
        return flat
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get("variables"), (list, dict)):  # Figma REST: /v1/files/:key/variables/local
        variables = data["variables"].values() if isinstance(data["variables"], dict) else data["variables"]
        for var in variables:
            modes = var.get("valuesByMode") or var.get("resolvedValuesByMode") or {}
            first = next(iter(modes.values()), None)
            first = first.get("resolvedValue", first) if isinstance(first, dict) and "resolvedValue" in first else first
            flat[_norm(var.get("name", ""))] = {"value": first, "type": (var.get("resolvedType") or "").lower() or None}
        return flat

    def walk(node, prefix, inherited):
        if not isinstance(node, dict):
            return
        t = node.get("$type", inherited)
        if "$value" in node or "value" in node and not isinstance(node.get("value"), dict):
            flat[_norm(prefix)] = {"value": node.get("$value", node.get("value")), "type": t or node.get("type")}
            return
        for k, v in node.items():
            if not k.startswith("$"):
                walk(v, f"{prefix}.{k}" if prefix else k, t)
    walk(data, "", None)
    for k, v in flat.items():
        v["value"] = _resolve(flat, v["value"])
    return flat


# ---------------------------------------------------------------- mapping
def map_roles(flat, overrides=None):
    """{role: (token name, value)} for the colour and font roles, by explicit mapping first, then by name hints."""
    out, overrides = {}, overrides or {}
    colours = {k: _hex(v["value"]) for k, v in flat.items() if _hex(v["value"])}
    fonts = {k: _font(v["value"]) for k, v in flat.items() if (v.get("type") in ("fontfamily", "fontFamily", "string", "typography") or "font" in k)
             and not _hex(v["value"]) and _font(v["value"]) and not re.search(r"(size|weight|height|spacing)", k)}
    for role in ROLES + FONT_ROLES:
        pool = colours if role in ROLES else fonts
        if role in overrides:
            name = _norm(overrides[role])
            if name not in pool:
                raise SystemExit(f"--map {role}={overrides[role]}: token not found or not a {'colour' if role in ROLES else 'font'}")
            out[role] = (name, pool[name])
            continue
        for hint in HINTS[role]:
            hit = next((k for k in pool if re.search(hint, k) and (k, pool[k]) not in out.values()), None)
            if hit:
                out[role] = (hit, pool[hit])
                break
    if "primary" in out and "primary_2" not in out:   # a darker shade of the primary keeps the header readable
        out["primary_2"] = ("(dérivée de primary)", shade(out["primary"][1], 0.82))
    return out


def shade(hex_colour, f):
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{max(0, min(255, round(x * f))):02X}" for x in (r, g, b))


# ---------------------------------------------------------------- accessibility
def luminance(hex_colour):
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def check(colors):
    """WCAG 2.1 checks on how the roles are used: [(level, message)]. level = error (unreadable) or warning."""
    out = []
    white = "#FFFFFF"
    for role, need, use in (("primary", 4.5, "texte blanc sur l'en-tête"), ("primary_2", 4.5, "texte blanc sur les bandeaux"),
                            ("primary", 4.5, "titres et liens sur fond blanc")):
        c = colors.get(role)
        if c and contrast(c, white) < need:
            out.append(("error", f"{role} {c} : contraste {contrast(c, white):.1f}:1 pour {use}, il faut au moins {need}:1 (WCAG AA)"))
    for role in ("accent", "secondary"):
        c = colors.get(role)
        if c and contrast(c, white) < 3:
            out.append(("warning", f"{role} {c} : contraste {contrast(c, white):.1f}:1 sur fond blanc (< 3:1), réservé aux aplats et graphiques"))
    return out


# ---------------------------------------------------------------- applying
def tokens_path(brand_dir):
    return Path(brand_dir) / "design" / "tokens.json"


def apply(brand, brand_dir):
    """brand.yaml values overridden by brands/<b>/design/tokens.json when present (returns a new dict)."""
    p = tokens_path(brand_dir)
    if not p.exists():
        return brand
    t = json.loads(p.read_text(encoding="utf-8"))
    b = dict(brand)
    b["colors"] = {**brand.get("colors", {}), **{k: v["$value"] for k, v in t.get("color", {}).items() if k in ROLES}}
    b["fonts"] = {**brand.get("fonts", {}), **{k: v["$value"] for k, v in t.get("font", {}).items() if k in FONT_ROLES}}
    b["design_source"] = t.get("$description", "")
    return b


def write(brand_dir, roles, source):
    t = {"$description": f"Importé de {Path(source).name} par install/design.py",
         "color": {r: {"$type": "color", "$value": v, "$extensions": {"source": n}} for r, (n, v) in roles.items() if r in ROLES},
         "font": {r: {"$type": "fontFamily", "$value": v, "$extensions": {"source": n}} for r, (n, v) in roles.items() if r in FONT_ROLES}}
    p = tokens_path(brand_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(t, indent=1, ensure_ascii=False), encoding="utf-8")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description="Importer ou vérifier le design system d'une marque")
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="lire des tokens (DTCG / Figma / CSS) et les appliquer à la marque")
    imp.add_argument("--brand", required=True)
    imp.add_argument("source")
    imp.add_argument("--map", action="append", default=[], help="rôle=token, ex. primary=color.brand.900")
    chk = sub.add_parser("check", help="vérifier la lisibilité des couleurs de la marque")
    chk.add_argument("--brand", required=True)
    a = ap.parse_args(argv)
    import yaml
    bdir = ROOT / "brands" / a.brand
    if not (bdir / "brand.yaml").exists():
        raise SystemExit(f"marque inconnue : brands/{a.brand}/brand.yaml introuvable")
    if a.cmd == "import":
        flat = read_tokens(a.source)
        if not flat:
            raise SystemExit(f"aucun token lu dans {a.source} (formats acceptés : DTCG JSON, export Figma, CSS avec variables --nom)")
        roles = map_roles(flat, dict(m.split("=", 1) for m in a.map))
        print(f"{len(flat)} tokens lus dans {a.source}\n")
        for r in ROLES + FONT_ROLES:
            print(f"  {r:<10} {roles[r][1]:<12} ← {roles[r][0]}" if r in roles else f"  {r:<10} (non trouvé : la valeur de brand.yaml est gardée ; --map {r}=<token>)")
        issues = check({r: v for r, (_, v) in roles.items() if r in ROLES})
        for level, msg in issues:
            print(("  ✗ " if level == "error" else "  ! ") + msg)
        if any(l == "error" for l, _ in issues):
            raise SystemExit("\nimport refusé : corrigez les couleurs illisibles (ou choisissez un autre token avec --map)")
        print(f"\nécrit : {write(bdir, roles, a.source).relative_to(ROOT)} · redéployez pour l'appliquer (install/install.py)")
    else:
        brand = apply(yaml.safe_load(open(bdir / "brand.yaml", encoding="utf-8")), bdir)
        issues = check(brand["colors"])
        for level, msg in issues or [("ok", "couleurs lisibles (WCAG AA)")]:
            print(f"{level:<8} {msg}")
        return 1 if any(l == "error" for l, _ in issues) else 0


if __name__ == "__main__":
    sys.exit(main())
