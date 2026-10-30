"""Brand-colored Office templates for the assistant: a PowerPoint deck and a Word 8D report.

The structure matches what app/renderers.py expects:
- deck: slide 0 = cover (its last text box holds the title paragraph and the subtitle paragraph),
  slides 1..4 = content slides, each with a title box, a body box whose text is exactly "[contenu]",
  a footer box whose text starts with "<company> ·", and one picture (the wordmark, re-inserted by the renderer).
- 8D: a meta table whose first column holds 'N° 8D', "Date d'ouverture", 'Pilote', 'Ligne / Poste',
  then Heading 1 paragraphs starting with D1..D8, each followed by one grey placeholder paragraph.

Usage: build(brand, brand_dir, out_dir) -> {"pptx": "templates/deck.pptx", "docx_8d": "templates/8d.docx", "wordmark": "templates/wordmark.png"}
"""
import shutil
import sys
from pathlib import Path

from docx import Document
from docx.shared import Cm, Pt, RGBColor
from pptx import Presentation
from pptx.dml.color import RGBColor as PRGB
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt as PPt

GREY = "5F6B7A"


def _hex(c):
    return c.lstrip("#").upper()


def _rect(s, x, y, w, h, color):
    r = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    r.fill.solid()
    r.fill.fore_color.rgb = PRGB.from_string(color)
    r.line.fill.background()
    return r


def _text(s, x, y, w, h, t, size, color, font, bold=False):
    tf = s.shapes.add_textbox(x, y, w, h).text_frame
    tf.word_wrap = True
    tf.text = t
    r = tf.paragraphs[0].runs[0]
    r.font.size, r.font.color.rgb, r.font.bold, r.font.name = PPt(size), PRGB.from_string(color), bold, font
    return tf


def _aspect(path):
    from PIL import Image
    w, h = Image.open(path).size
    return w / h


def _picture_height_for_width(path, max_w_in, max_h_in):
    from PIL import Image
    w, h = Image.open(path).size
    return min(max_h_in, max_w_in * h / w)


def build_deck(brand, brand_dir, out_path):
    c = brand["colors"]
    primary, primary2, accent = _hex(c["primary"]), _hex(c.get("primary_2", c["primary"])), _hex(c["accent"])
    font = brand.get("fonts", {}).get("office", "Calibri")
    company, site = brand["company"], brand.get("site", "")
    org = brand.get("org_label", "")
    logo_dark_bg = brand_dir / brand["logos"]["wordmark_on_dark"]
    logo_light_bg = brand_dir / brand["logos"]["wordmark"]

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    W, H = prs.slide_width, prs.slide_height

    # cover: brand panel on the left with the wordmark, title on white
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _rect(s, 0, 0, Inches(4.6), H, primary)
    _rect(s, Inches(4.6), 0, Inches(0.12), H, accent)
    lh = _picture_height_for_width(logo_dark_bg, 3.2, 0.9)
    lw = lh * _aspect(logo_dark_bg)
    s.shapes.add_picture(str(logo_dark_bg), Inches((4.6 - lw) / 2), Inches(3.75 - lh / 2), width=Inches(lw), height=Inches(lh))
    _text(s, Inches(5.4), Inches(6.65), Inches(7.4), Inches(0.4), " · ".join(x for x in (company, site, org) if x), 11, GREY, font)
    tf = _text(s, Inches(5.4), Inches(2.5), Inches(7.4), Inches(1.6), "Titre de la présentation", 34, primary2, font, True)
    p = tf.add_paragraph()
    p.text = f"Sous-titre · {org or company}"
    p.runs[0].font.size, p.runs[0].font.color.rgb, p.runs[0].font.name = PPt(18), PRGB.from_string(GREY), font

    # content slides: thin brand band, accent tick, wordmark top-right, footer "<company> · ..."
    wh = _picture_height_for_width(logo_light_bg, 2.4, 0.26)
    ww = wh * _aspect(logo_light_bg)
    for t in ["Titre de la diapositive", "Titre de la diapositive", "Titre de la diapositive", "Titre de la diapositive"]:
        s = prs.slides.add_slide(prs.slide_layouts[6])
        _rect(s, 0, 0, W, Inches(0.09), primary)
        _rect(s, Inches(0.6), Inches(1.18), Inches(0.9), Inches(0.05), accent)
        s.shapes.add_picture(str(logo_light_bg), Inches(13.333 - 0.6 - ww), Inches(0.42), width=Inches(ww), height=Inches(wh))
        _text(s, Inches(0.6), Inches(0.35), Inches(9.6), Inches(0.9), t, 26, primary2, font, True)
        _text(s, Inches(0.6), Inches(1.5), Inches(12), Inches(5), "[contenu]", 16, GREY, font)
        _text(s, Inches(0.6), Inches(7.08), Inches(8), Inches(0.3), " · ".join(x for x in (company, site, "Diffusion interne") if x), 8, GREY, font)
    prs.save(out_path)


def build_8d(brand, brand_dir, out_path):
    c = brand["colors"]
    primary2 = _hex(c.get("primary_2", c["primary"]))
    accent = _hex(c["accent"])
    font = brand.get("fonts", {}).get("office", "Calibri")
    company, site = brand["company"], brand.get("site", "")
    logo_light_bg = brand_dir / brand["logos"]["wordmark"]

    d = Document()
    st = d.styles["Normal"]
    st.font.name, st.font.size = font, Pt(10.5)
    for name, size in (("Title", 22), ("Heading 1", 13)):
        hs = d.styles[name]
        hs.font.name, hs.font.color.rgb, hs.font.size = font, RGBColor.from_string(primary2), Pt(size)
    sec = d.sections[0]
    sec.left_margin = sec.right_margin = Cm(2)
    hp = sec.header.paragraphs[0]
    hp.add_run().add_picture(str(logo_light_bg), width=Cm(0.55 * _aspect(logo_light_bg)), height=Cm(0.55))
    hr = hp.add_run("\t" + " · ".join(x for x in ("Qualité", site, "Diffusion interne") if x))
    hr.font.size = Pt(8)
    fp = sec.footer.paragraphs[0]
    fp.text = f"{company} · Rapport 8D · document à relire avant diffusion"
    fp.runs[0].font.size, fp.runs[0].font.color.rgb = Pt(7.5), RGBColor.from_string(GREY)

    d.add_paragraph("Rapport 8D · Résolution de problème", style="Title")
    meta = d.add_table(rows=4, cols=2)
    meta.style = "Table Grid"
    for i, k in enumerate(["N° 8D", "Date d'ouverture", "Pilote", "Ligne / Poste"]):
        meta.cell(i, 0).text = k
        run = meta.cell(i, 0).paragraphs[0].runs[0]
        run.bold, run.font.color.rgb = True, RGBColor.from_string(primary2)
    for title, hint in [
        ("D1 · Équipe", "Membres, rôles"),
        ("D2 · Description du problème", "QQOQCP : quoi, qui, où, quand, combien, comment. Chiffres à l'appui."),
        ("D3 · Actions de confinement", "Actions immédiates, pièces concernées, quarantaine"),
        ("D4 · Analyse des causes racines", "5 pourquoi / Ishikawa, cause de non-détection"),
        ("D5 · Actions correctives retenues", ""),
        ("D6 · Mise en œuvre et validation", "Indicateurs de vérification"),
        ("D7 · Prévention de la récurrence", "Standards, AMDEC, transversalisation"),
        ("D8 · Clôture et félicitations", ""),
    ]:
        h = d.add_heading(title, 1)
        h.runs[0].font.color.rgb = RGBColor.from_string(primary2)
        p = d.add_paragraph(f"[{hint}]" if hint else "[à compléter]")
        p.runs[0].font.color.rgb = RGBColor(0x98, 0xA2, 0xB3)
        p.runs[0].font.size = Pt(9)
    # thin accent rule under the title, as in the deck
    d.paragraphs[0].paragraph_format.space_after = Pt(6)
    d.paragraphs[0].runs[0].font.color.rgb = RGBColor.from_string(primary2)
    _ = accent
    d.save(out_path)


def build(brand: dict, brand_dir: Path, out_dir: Path) -> dict:
    brand_dir, out_dir = Path(brand_dir), Path(out_dir)
    (out_dir / "templates").mkdir(parents=True, exist_ok=True)
    build_deck(brand, brand_dir, out_dir / "templates" / "deck.pptx")
    build_8d(brand, brand_dir, out_dir / "templates" / "8d.docx")
    # the renderer re-inserts this picture on every content slide and in Word headers (light background)
    shutil.copy(brand_dir / brand["logos"]["wordmark"], out_dir / "templates" / "wordmark.png")
    shutil.copy(brand_dir / brand["logos"]["wordmark_on_dark"], out_dir / "templates" / "wordmark_on_dark.png")
    return {"pptx": "templates/deck.pptx", "docx_8d": "templates/8d.docx", "wordmark": "templates/wordmark.png",
            "wordmark_on_dark": "templates/wordmark_on_dark.png"}


if __name__ == "__main__":  # python3 install/office_templates.py brands/acme /tmp/out
    import yaml
    bd = Path(sys.argv[1])
    print(build(yaml.safe_load(open(bd / "brand.yaml", encoding="utf-8")), bd, Path(sys.argv[2])))
