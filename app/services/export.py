"""Pure Courseware -> 4:3 PPTX builder (deep-blue + gold classroom theme).

No FastAPI, no DB, no network. The slide geometry is pinned explicitly so the
classroom display format (10 x 7.5 inches) is stable and testable rather than
relying on a library default that could change. Missing fields are skipped;
narration goes into speaker notes, not onto the visible slide.
"""

from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile, ZipInfo

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

# The blackboard's middle screen is 4:3 — pin it, do not rely on the default.
SLIDE_WIDTH = Inches(10)
SLIDE_HEIGHT = Inches(7.5)
AIGC_LABEL = "AI辅助生成"

# Palette (design §2) — gold is an accent only, never a background.
DEEP_BLUE = RGBColor(0x0F, 0x2A, 0x47)
SECONDARY_BLUE = RGBColor(0x1B, 0x3E, 0x5C)
GOLD = RGBColor(0xC9, 0xA2, 0x27)
LIGHT_GOLD = RGBColor(0xE8, 0xD0, 0x8D)
CREAM = RGBColor(0xF7, 0xF4, 0xEC)
BODY_DARK = RGBColor(0x1F, 0x29, 0x33)
MUTED = RGBColor(0x6B, 0x72, 0x80)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)


def _add_rect(slide, x, y, w, h, color):
    """Solid, borderless rectangle — the building block of the theme."""
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()


def _add_text(slide, x, y, w, h, lines, size, color,
              bold=False, align=None, spacing=0):
    """Add a text box; each string in ``lines`` becomes one paragraph."""
    box = slide.shapes.add_textbox(x, y, w, h)
    frame = box.text_frame
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = Inches(0.05)
    frame.margin_top = frame.margin_bottom = Inches(0.02)
    for index, line in enumerate(lines):
        para = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        if align is not None:
            para.alignment = align
        para.space_after = Pt(spacing)
        run = para.add_run()
        run.text = line
        run.font.size = Pt(size)
        run.font.color.rgb = color
        run.font.bold = bold


def _aigc_label(aigc) -> str:
    """Accept the validated ``{"label": ..., ...}`` shape and plain strings."""
    if isinstance(aigc, dict):
        label = aigc.get("label")
        if label:
            return str(label)
        return AIGC_LABEL if aigc else ""
    if isinstance(aigc, str):
        return aigc
    return AIGC_LABEL if aigc else ""


def _text_of(value) -> str:
    return str(value) if value is not None else ""


def _save(prs) -> bytes:
    """Write the package with fixed ZIP timestamps for byte-identical replay."""
    raw = BytesIO()
    prs.save(raw)
    raw.seek(0)
    out = BytesIO()
    with ZipFile(raw) as src, ZipFile(out, "w") as dst:
        for item in src.infolist():
            info = ZipInfo(item.filename, (1980, 1, 1, 0, 0, 0))
            info.compress_type = item.compress_type
            info.external_attr = item.external_attr
            dst.writestr(info, src.read(item.filename))
    return out.getvalue()


def build_pptx(courseware: dict) -> bytes:
    """Build a themed 4:3 .pptx for a Courseware dict and return its bytes.

    Slide 1 is a deep-blue cover with gold accents; every scene gets a cream
    slide with a deep-blue title band, a gold sequence number and gold bullets.
    Narration lives in the speaker notes. Empty fields are simply omitted.
    """
    cw = courseware if isinstance(courseware, dict) else {}
    prs = Presentation()
    prs.slide_width = SLIDE_WIDTH
    prs.slide_height = SLIDE_HEIGHT
    aigc_text = _aigc_label(cw.get("aigc"))

    # --- cover (explicit shapes, no default title layout) ---------------------
    cover = prs.slides.add_slide(prs.slide_layouts[6])
    _add_rect(cover, 0, 0, SLIDE_WIDTH, SLIDE_HEIGHT, DEEP_BLUE)
    _add_rect(cover, Inches(1.2), Inches(1.9), Inches(7.6), Pt(3), GOLD)
    _add_text(cover, Inches(0.8), Inches(2.25), Inches(8.4), Inches(1.4),
              [_text_of(cw.get("title")) or "未命名课件"],
              36, WHITE, bold=True, align=PP_ALIGN.CENTER)

    cover_lines = []
    subject = _text_of(cw.get("subject")).strip()
    grade = _text_of(cw.get("grade")).strip()
    if subject:
        cover_lines.append(f"学科：{subject}")
    if grade:
        cover_lines.append(f"年级：{grade}")
    if aigc_text:
        cover_lines.append(aigc_text)
    if cover_lines:
        _add_text(cover, Inches(0.8), Inches(3.9), Inches(8.4), Inches(1.2),
                  cover_lines, 14, LIGHT_GOLD, align=PP_ALIGN.CENTER, spacing=6)
    _add_rect(cover, Inches(3.0), Inches(6.7), Inches(4.0), Inches(0.06), GOLD)

    # --- one themed slide per scene ------------------------------------------
    scenes = cw.get("scenes")
    if not isinstance(scenes, list):
        scenes = []
    total = len(scenes)
    for index, scene in enumerate(scenes, start=1):
        scene = scene if isinstance(scene, dict) else {}
        slide = prs.slides.add_slide(prs.slide_layouts[6])

        # Cream background + deep-blue title band with a gold vertical bar.
        _add_rect(slide, 0, 0, SLIDE_WIDTH, SLIDE_HEIGHT, CREAM)
        _add_rect(slide, 0, 0, SLIDE_WIDTH, Inches(1.1), DEEP_BLUE)
        _add_rect(slide, 0, 0, Inches(0.12), Inches(1.1), GOLD)
        scene_title = _text_of(scene.get("title"))
        title_pt = 24 if len(scene_title) > 16 else 26
        _add_text(slide, Inches(0.4), Inches(0.2), Inches(7.8), Inches(0.75),
                  [scene_title], title_pt, WHITE, bold=True)
        if total:
            _add_text(slide, Inches(8.3), Inches(0.3), Inches(1.4), Inches(0.5),
                      [f"{index:02d} / {total:02d}"], 14, GOLD,
                      bold=True, align=PP_ALIGN.RIGHT)

        # Bullets with gold markers, wrapped and shrunk to fit the 4:3 slide.
        bullets = scene.get("bullets")
        if not isinstance(bullets, list):
            bullets = []
        bullets = [_text_of(b).strip() for b in bullets if _text_of(b).strip()]
        total_chars = sum(len(b) for b in bullets)
        if total_chars > 600:
            font_pt = 13
        elif total_chars > 350:
            font_pt = 15
        elif total_chars > 180:
            font_pt = 16
        else:
            font_pt = 18

        content = slide.shapes.add_textbox(
            Inches(0.6), Inches(1.45), Inches(8.8), Inches(5.1)
        )
        frame = content.text_frame
        frame.word_wrap = True
        for offset, bullet in enumerate(bullets):
            para = frame.paragraphs[0] if offset == 0 else frame.add_paragraph()
            para.space_after = Pt(10)
            marker = para.add_run()
            marker.text = "● "
            marker.font.size = Pt(font_pt)
            marker.font.color.rgb = GOLD
            marker.font.bold = True
            run = para.add_run()
            run.text = bullet
            run.font.size = Pt(font_pt)
            run.font.color.rgb = BODY_DARK

        # Footer: thin gold rule, AIGC label and page number.
        _add_rect(slide, Inches(0.6), Inches(6.82), Inches(8.8), Pt(1), GOLD)
        if aigc_text:
            _add_text(slide, Inches(0.6), Inches(6.95), Inches(5), Inches(0.35),
                      [aigc_text], 9, MUTED)
        _add_text(slide, Inches(8.3), Inches(6.95), Inches(1.1), Inches(0.35),
                  [str(index)], 9, MUTED, align=PP_ALIGN.RIGHT)

        # Speaker notes: visible to the teacher presenting, never on the slide.
        narration = scene.get("narration")
        slide.notes_slide.notes_text_frame.text = (
            _text_of(narration) if narration is not None else ""
        )

    return _save(prs)
