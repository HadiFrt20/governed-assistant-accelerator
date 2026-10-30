"""Dev helper: assemble a local build of the app for a brand (no Databricks calls), to run preflight and render samples.
Usage: python tests/local_build.py <brand> <out_dir>"""
import json, shutil, sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "install"))
import office_templates  # noqa: E402
import settings  # noqa: E402,F401


def build(brand_name, out):
    cfg = yaml.safe_load(open(ROOT / "config/assistant.yaml"))
    bdir = ROOT / "brands" / brand_name
    brand = yaml.safe_load(open(bdir / "brand.yaml"))
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "app", out)
    (out / "static/brand").mkdir(parents=True, exist_ok=True)
    for f in set(brand["logos"].values()):
        shutil.copy(bdir / f, out / "static/brand" / f)
    templates = office_templates.build(brand, bdir, out)
    import settings as shared_settings  # the installer's own builder: the tested build is the deployed build
    settings = shared_settings.build(cfg, brand, templates, me="curator@example.com")
    json.dump(settings, open(out / "settings.json", "w"), ensure_ascii=False, indent=1)
    return out


if __name__ == "__main__":
    print(build(sys.argv[1], sys.argv[2]))
