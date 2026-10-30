"""Guided setup: a few questions in plain language, everything technical detected or defaulted.

    ./assistant init            (or: uv run --python 3.11 --with-requirements requirements-dev.txt python install/init.py)

Asks: the company name, the assistant's name, an optional brand colour and logo. Detects: the Databricks connection (offers
to sign in when there is none), a SQL warehouse, a catalog where the assistant can live. Writes brands/<slug>/ (brand pack with
logos) and config/<slug>.local.yaml (about ten lines; every other setting has a safe default in install/defaults.yaml).
"""
import argparse
import configparser
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import design  # noqa: E402

DEFAULT_PRIMARY = "#0E2A47"
SKIP_CATALOGS = {"system", "samples", "hive_metastore", "__databricks_internal"}


def slugify(text, sep="-", max_len=20):
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", sep, t).strip(sep)[:max_len].strip(sep) or "entreprise"


def readable_primary(hex_colour):
    """Darken a brand colour until white text on it is readable (WCAG AA 4.5:1); returns (colour, changed)."""
    c, changed = hex_colour.upper(), False
    while design.contrast(c, "#FFFFFF") < 4.5:
        c, changed = design.shade(c, 0.9), True
    return c, changed


# ---------------------------------------------------------------- brand pack
def make_logos(out_dir, company, primary, logo=None):
    """wordmark.png, wordmark_on_dark.png, mark.png: from the customer's logo when given, else drawn from the name."""
    from PIL import Image, ImageDraw, ImageFont
    out_dir.mkdir(parents=True, exist_ok=True)
    if logo:
        img = Image.open(logo).convert("RGBA")
        img.thumbnail((900, 240))
        img.save(out_dir / "wordmark.png")
        img.save(out_dir / "wordmark_on_dark.png")
        side = min(img.size)
        sq = img.crop(((img.width - side) // 2, (img.height - side) // 2, (img.width + side) // 2, (img.height + side) // 2)).resize((256, 256))
        sq.save(out_dir / "mark.png")
        return
    try:
        font = ImageFont.load_default(size=96)
        small = ImageFont.load_default(size=150)
    except TypeError:  # Pillow < 10.1
        font = small = ImageFont.load_default()
    for name, colour in (("wordmark.png", primary), ("wordmark_on_dark.png", "#FFFFFF")):
        probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        box = probe.textbbox((0, 0), company.upper(), font=font)
        img = Image.new("RGBA", (box[2] - box[0] + 40, box[3] - box[1] + 40), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((20 - box[0], 20 - box[1]), company.upper(), font=font, fill=colour)
        img.save(out_dir / name)
    mark = Image.new("RGBA", (256, 256), primary)
    d = ImageDraw.Draw(mark)
    letter = (company.strip()[:1] or "A").upper()
    box = d.textbbox((0, 0), letter, font=small)
    d.text(((256 - (box[2] - box[0])) / 2 - box[0], (256 - (box[3] - box[1])) / 2 - box[1]), letter, font=small, fill="#FFFFFF")
    mark.save(out_dir / "mark.png")


def brand_yaml(company, assistant, primary):
    p, _ = readable_primary(primary)
    return {
        "name": assistant, "company": company, "site": "Siège", "site_short": "Siège", "org_label": "Direction générale", "language": "fr",
        "description": f"l'assistant interne de {company}",
        "colors": {"primary": p, "primary_2": design.shade(p, 0.82), "accent": "#0F766E", "secondary": "#2B6CB0"},
        "fonts": {"heading": "Inter", "body": "Inter", "office": "Calibri"},
        "logos": {"wordmark": "wordmark.png", "wordmark_on_dark": "wordmark_on_dark.png", "mark": "mark.png", "favicon": "mark.png"},
        "departments": ["Direction", "Opérations", "Finance", "Ressources humaines"],
        "templates": [
            {"id": "synthese", "kind": "docx", "name": "Note de synthèse", "desc": "Une page pour la direction",
             "prompt": "Rédige une note de synthèse d'une page sur le sujet suivant : "},
            {"id": "presentation", "kind": "pptx", "name": "Présentation", "desc": f"Diapositives aux couleurs de {company}",
             "prompt": "Prépare une présentation de 4 diapositives : contexte, chiffres clés, risques, prochaines étapes, sur : "},
            {"id": "tableau", "kind": "html", "name": "Tableau de bord interactif", "desc": f"Page web au design {company}",
             "prompt": "Construis un tableau de bord interactif à partir des données suivantes : "}],
        "footer": f"Vos droits d'accès s'appliquent · données et documents restent chez {company}",
        "instructions": "",
        "suggestions": {
            "Direction": ["Rédige une note de synthèse à partir du document joint", "Prépare 3 diapositives sur les priorités du trimestre"],
            "Opérations": ["Résume la procédure jointe en 5 points", "Trouve dans la bibliothèque la procédure qui traite de ce sujet"],
            "Finance": ["Prépare une présentation des écarts budgétaires du mois", "Rédige un mail de relance fournisseur"],
            "Ressources humaines": ["Rédige une note d'information aux équipes", "Résume le compte rendu de réunion joint"]},
    }


# ---------------------------------------------------------------- workspace detection
def profiles():
    p = Path(os.environ.get("DATABRICKS_CONFIG_FILE", Path.home() / ".databrickscfg"))
    cp = configparser.ConfigParser()
    if p.exists():
        cp.read(p)
    return [s for s in cp.sections() if cp[s].get("host")]


def pick_warehouse(warehouses):
    """Prefer serverless, then running, then anything; returns (id, name) or None."""
    ranked = sorted(warehouses, key=lambda w: (not getattr(w, "enable_serverless_compute", False),
                                               str(getattr(getattr(w, "state", None), "value", "")) != "RUNNING", w.name or ""))
    return (ranked[0].id, ranked[0].name) if ranked else None


def candidate_catalogs(catalogs, me):
    """Catalogs that can hold the assistant's schema: managed ones (not system, shared or foreign), the user's own first."""
    def ok(c):
        kind = str(getattr(getattr(c, "catalog_type", None), "value", "") or "")
        return c.name not in SKIP_CATALOGS and not c.name.startswith("__") and kind in ("", "MANAGED_CATALOG")
    names = [c.name for c in catalogs if ok(c)]
    owned = [c.name for c in catalogs if ok(c) and getattr(c, "owner", "") == me]
    return owned + [n for n in names if n not in owned]


# ---------------------------------------------------------------- writing
def create(answers, root=ROOT):
    """answers: company, assistant, primary (optional), logo (optional), profile, warehouse_id, catalog. Returns (config, brand_dir)."""
    slug = slugify(answers["company"], "_")
    bdir = root / "brands" / slug
    if bdir.exists() and not answers.get("overwrite"):
        raise SystemExit(f"la marque brands/{slug} existe déjà : relancez avec --overwrite pour la remplacer")
    if bdir.exists():
        shutil.rmtree(bdir)
    brand = brand_yaml(answers["company"], answers["assistant"], answers.get("primary") or DEFAULT_PRIMARY)
    make_logos(bdir, answers["company"], brand["colors"]["primary"], answers.get("logo"))
    (bdir / "brand.yaml").write_text("# Créé par install/init.py : textes, couleurs et suggestions modifiables ici (docs/CUSTOMIZE.md).\n"
                                     + yaml.safe_dump(brand, allow_unicode=True, sort_keys=False), encoding="utf-8")
    app = f"{slugify(answers['assistant'] if answers['assistant'] else answers['company'], '-', 19)}-assistant"[:30].strip("-")
    cfg = {"workspace": {"profile": answers["profile"], "warehouse_id": answers["warehouse_id"]}, "brand": slug,
           "unity_catalog": {"catalog": answers["catalog"]}, "app": {"name": app}}
    path = root / "config" / f"{slug}.local.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text("# Créé par install/init.py. Tout le reste a une valeur par défaut sûre (install/defaults.yaml) ;\n"
                    "# pour l'ajuster, copiez la clé depuis config/assistant.yaml (référence commentée).\n"
                    + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path, bdir


def ask(question, default=None, check=None):
    while True:
        suffix = f" [{default}]" if default else ""
        v = input(f"{question}{suffix} : ").strip() or (default or "")
        if not v:
            print("  réponse nécessaire")
            continue
        err = check(v) if check else None
        if not err:
            return v
        print(f"  {err}")


def choose(question, options, default=0):
    for i, (_, label) in enumerate(options, 1):
        print(f"  {i}. {label}")
    v = ask(question, str(default + 1), lambda x: None if x.isdigit() and 1 <= int(x) <= len(options) else f"un numéro entre 1 et {len(options)}")
    return options[int(v) - 1][0]


def interactive(args):
    print("\nInstallation de l'assistant interne : quelques questions, environ 2 minutes.\n")
    company = args.company or ask("Nom de votre entreprise")
    assistant = args.assistant or ask("Nom de l'assistant (ce que verront vos équipes)", f"Assistant {company}"[:40])
    primary = args.color or input("Couleur principale de votre marque, ex. #0E2A47 (Entrée = bleu foncé) : ").strip() or DEFAULT_PRIMARY
    if not design.HEX.match(primary):
        print(f"  « {primary} » n'est pas une couleur #RRGGBB : bleu foncé utilisé")
        primary = DEFAULT_PRIMARY
    fixed, changed = readable_primary(primary)
    if changed:
        print(f"  {primary} est trop clair pour du texte blanc : {fixed} sera utilisé (même teinte, plus foncée)")
    logo = args.logo or input("Logo (chemin d'un fichier PNG ou JPG, Entrée = logo généré à partir du nom) : ").strip() or None
    if logo and not Path(logo).exists():
        print(f"  {logo} introuvable : logo généré à partir du nom")
        logo = None

    profs = profiles()
    if args.profile:
        profile = args.profile
    elif not profs:
        host = ask("Adresse de votre espace Databricks (ex. https://xxx.cloud.databricks.com)", check=lambda h: None if h.startswith("https://") else "commence par https://")
        profile = slugify(company)
        print("  Une page de connexion Databricks va s'ouvrir dans votre navigateur.")
        subprocess.run(["databricks", "auth", "login", "--host", host, "-p", profile], check=True)
    elif len(profs) == 1:
        profile = profs[0]
    else:
        print("\nConnexion Databricks à utiliser :")
        profile = choose("Numéro", [(p, p) for p in profs])

    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient(profile=profile)
    me = w.current_user.me().user_name
    print(f"\nConnecté en tant que {me} sur {w.config.host}")
    wh = (args.warehouse, args.warehouse) if args.warehouse else pick_warehouse(list(w.warehouses.list()))
    if not wh:
        raise SystemExit("aucun SQL warehouse dans cet espace : créez-en un (SQL > SQL Warehouses > Créer, type serverless) puis relancez")
    print(f"SQL warehouse : {wh[1]}")
    if args.catalog:
        catalog = args.catalog
    else:
        cats = candidate_catalogs(list(w.catalogs.list()), me)
        if not cats:
            raise SystemExit("aucun catalogue utilisable : demandez à un administrateur de vous créer un catalogue, puis relancez")
        print("\nCatalogue Unity Catalog où installer l'assistant (il y crée son propre schéma) :")
        catalog = choose("Numéro", [(c, c) for c in cats[:12]])
    return {"company": company, "assistant": assistant, "primary": fixed, "logo": logo, "profile": profile,
            "warehouse_id": wh[0], "catalog": catalog, "overwrite": args.overwrite}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Installation guidée de l'assistant")
    for a in ("company", "assistant", "color", "logo", "profile", "warehouse", "catalog"):
        ap.add_argument(f"--{a}")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)
    answers = interactive(args)
    cfg, bdir = create(answers)
    print(f"\nPrêt.\n  marque        {bdir.relative_to(ROOT)}/ (textes et suggestions modifiables dans brand.yaml)\n"
          f"  configuration {cfg.relative_to(ROOT)}\n\nÉtape suivante :\n  ./assistant deploy {cfg.relative_to(ROOT)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
