"""Render docs/architecture/architecture.html to docs/architecture.png (2x).  uv run --with playwright python docs/architecture/render.py"""
from pathlib import Path

from playwright.sync_api import sync_playwright

here = Path(__file__).resolve().parent
with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1600, "height": 1100}, device_scale_factor=2)
    page.goto((here / "architecture.html").as_uri())
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready")
    page.locator("#canvas").screenshot(path=str(here.parent / "architecture.png"))
    b.close()
print("docs/architecture.png")
