"""Pure Courseware -> 4:3 PPTX builder.

No FastAPI, no DB, no network. The slide geometry is pinned explicitly so the
classroom display format (10 x 7.5 inches) is stable and testable rather than
relying on a library default that could change. Missing fields are skipped;
narration goes into speaker notes, not onto the visible slide.
"""

from __future__ import annotations

from io import BytesIO

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

# The blackboard's middle screen is 4:3 — pin it, do not rely on the default.
SLIDE_WIDTH = Inches(10)
SLIDE_HEIGHT = Inches(7.5)
AIGC_LABEL = "AI辅助生成"


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


def build_pptx(courseware: dict) -> bytes:
    """Build a 4:3 .pptx for a Courseware dict and return its bytes.

    Slide 1 is the cover (title / subject / grade / AIGC label); every scene
    gets its own slide with the title as heading and bullets as a list, and its
    narration in the speaker notes. Empty fields are simply omitted.
    """
    cw = courseware if isinstance(courseware, dict) else {}
    prs = Presentation()
    prs.slide_width = SLIDE_WIDTH
    prs.slide_height = SLIDE_HEIGHT

    # --- cover (Title Slide layout) -------------------------------------------
    cover = prs.slides.add_slide(prs.slide_layouts[0])
    cover.shapes.title.text = _text_of(cw.get("title")) or "未命名课件"
    cover_lines = []
    subject = _text_of(cw.get("subject")).strip()
    grade = _text_of(cw.get("grade")).strip()
    if subject:
        cover_lines.append(f"学科：{subject}")
    if grade:
        cover_lines.append(f"年级：{grade}")
    label = _aigc_label(cw.get("aigc"))
    if label:
        cover_lines.append(label)
    if cover_lines:
        cover.placeholders[1].text_frame.text = "\n".join(cover_lines)

    # --- one slide per scene (Title and Content layout) -----------------------
    scenes = cw.get("scenes")
    if not isinstance(scenes, list):
        scenes = []
    for scene in scenes:
        scene = scene if isinstance(scene, dict) else {}
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = _text_of(scene.get("title"))

        body = slide.placeholders[1].text_frame
        body.clear()
        body.word_wrap = True

        bullets = scene.get("bullets")
        if not isinstance(bullets, list):
            bullets = []
        bullets = [_text_of(b).strip() for b in bullets if _text_of(b).strip()]

        # Fit the text to a 4:3 slide: shrink font size as content grows so long
        # bullets never overflow past the white slide onto the grey canvas.
        total_chars = sum(len(b) for b in bullets)
        if total_chars > 600:
            font_pt = 13
        elif total_chars > 350:
            font_pt = 15
        elif total_chars > 180:
            font_pt = 17
        else:
            font_pt = 20

        first = True
        for bullet in bullets:
            paragraph = body.paragraphs[0] if first else body.add_paragraph()
            paragraph.text = bullet
            paragraph.level = 0
            paragraph.space_after = Pt(6)
            for run in paragraph.runs:
                run.font.size = Pt(font_pt)
                run.font.color.rgb = RGBColor(0x1A, 0x1A, 0x1A)
            first = False

        # Also size the title so a long heading does not dominate the slide.
        title_len = len(_text_of(scene.get("title")))
        title_pt = 24 if title_len > 16 else 28
        for run in slide.shapes.title.text_frame.paragraphs[0].runs:
            run.font.size = Pt(title_pt)
            run.font.bold = True

        # Speaker notes: visible to the teacher presenting, never on the slide.
        narration = scene.get("narration")
        slide.notes_slide.notes_text_frame.text = (
            _text_of(narration) if narration is not None else ""
        )

    buffer = BytesIO()
    prs.save(buffer)
    return buffer.getvalue()
