"""PPTX export: 4:3 output, scene/notes fidelity, free downloads, owner-only auth.

Export is included in generation (already charged by style tier at create_job),
so this endpoint must never touch the credit ledger.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_FILL
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Inches, Pt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.domain.ledger import append_entry
from app.main import app
from app.models import CreditBalance, CreditEntry, GenerationJob, User
from app.services.auth import issue_token
from app.services.export import build_pptx

PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)

DEEP_BLUE = RGBColor(0x0F, 0x2A, 0x47)
GOLD = RGBColor(0xC9, 0xA2, 0x27)
CREAM = RGBColor(0xF7, 0xF4, 0xEC)

COURSEWARE = {
    "version": "1.0",
    "title": "一元二次方程",
    "subject": "数学",
    "grade": "九年级",
    "scenes": [
        {
            "type": "slide",
            "title": "认识一元二次方程",
            "bullets": ["一般形式 ax²+bx+c=0", "a 不等于 0"],
            "narration": "先复习方程的概念，再引入一般形式。",
            "visual_hint": "",
        },
        {
            "type": "slide",
            "title": "求根公式",
            "bullets": ["判别式决定根的情况"],
            "narration": "用配方法推导求根公式。",
            "visual_hint": "",
        },
    ],
    "quiz": [],
    "aigc": {"label": "AI辅助生成", "provider": "deepseek", "model": "test"},
}


# --- fixtures ----------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    users = {}
    for name, phone, role in (
        ("teacher", "13800000001", "user"),
        ("other", "13800000002", "user"),
        ("admin", "13800000003", "admin"),
    ):
        user = User(
            phone=phone,
            display_name=name,
            role=role,
            is_active=True,
            invite_code=f"EXPORT-{name.upper()}",
        )
        session.add(user)
        session.flush()
        users[name] = user
        append_entry(
            session,
            user_id=user.id,
            delta=100,
            entry_type="grant",
            ref_type="signup",
            ref_id=str(user.id),
            idempotency_key=f"signup-grant:{user.id}",
        )
    session.commit()
    try:
        yield session, users
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db):
    session, _ = db

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _login(client, user):
    client.cookies.set("access_token", issue_token(user, channel="web"))


def _make_job(db, user, result=COURSEWARE):
    session, _ = db
    job = GenerationJob(
        user_id=user.id,
        spec={"topic": "一元二次方程", "type": "interactive"},
        status="succeeded",
        result=result,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _slide_text(slide) -> str:
    return "\n".join(
        shape.text_frame.text
        for shape in slide.shapes
        if shape.has_text_frame
    )


def _solid_shapes(slide):
    return [
        shape for shape in slide.shapes
        if shape.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE
        and shape.fill.type == MSO_FILL.SOLID
    ]


def _has_color(slide, rgb) -> bool:
    return any(shape.fill.fore_color.rgb == rgb for shape in _solid_shapes(slide))


# --- pure builder -------------------------------------------------------------


def test_build_pptx_is_4_3():
    prs = Presentation(io.BytesIO(build_pptx(COURSEWARE)))
    assert prs.slide_width == Inches(10)
    assert prs.slide_height == Inches(7.5)
    assert prs.slide_width / prs.slide_height == pytest.approx(4 / 3)


def test_long_bullets_use_smaller_font():
    """Long bullets shrink font size so text stays inside the 4:3 slide."""
    short = {"title": "短", "scenes": [{"title": "s", "bullets": ["短要点"]}]}
    long = {"title": "长", "scenes": [{"title": "s", "bullets": ["很长的要点" * 80]}]}

    def first_body_font(cw):
        prs = Presentation(io.BytesIO(build_pptx(cw)))
        for shape in prs.slides[1].shapes:
            if not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                if para.runs and para.runs[0].text.startswith("●"):
                    return para.runs[-1].font.size  # body run, not the gold marker
        raise AssertionError("no bullet body text found")

    assert first_body_font(long) <= first_body_font(short)
    assert first_body_font(long) <= Pt(15)


def test_cover_uses_deep_blue_and_gold():
    cover = Presentation(io.BytesIO(build_pptx(COURSEWARE))).slides[0]
    full_bleed = [
        shape for shape in _solid_shapes(cover)
        if shape.fill.fore_color.rgb == DEEP_BLUE
        and shape.width == Inches(10) and shape.height == Inches(7.5)
    ]
    assert full_bleed, "cover needs a full-bleed deep-blue background"
    assert _has_color(cover, GOLD), "cover needs a gold accent"
    assert "一元二次方程" in _slide_text(cover)


def test_scene_has_title_band_and_gold_sequence():
    slide = Presentation(io.BytesIO(build_pptx(COURSEWARE))).slides[1]
    assert _has_color(slide, CREAM), "scene needs its cream background"
    band = [
        shape for shape in _solid_shapes(slide)
        if shape.fill.fore_color.rgb == DEEP_BLUE and shape.height == Inches(1.1)
    ]
    assert band, "scene needs a deep-blue title band"
    bar = [
        shape for shape in _solid_shapes(slide)
        if shape.fill.fore_color.rgb == GOLD and shape.height == Inches(1.1)
    ]
    assert bar, "title band needs a gold vertical bar"
    assert "01 / 02" in _slide_text(slide), "gold sequence number missing"


def test_build_pptx_scenes_and_notes():
    prs = Presentation(io.BytesIO(build_pptx(COURSEWARE)))
    # Cover + one slide per scene.
    assert len(prs.slides) == 1 + len(COURSEWARE["scenes"])

    cover = _slide_text(prs.slides[0])
    for needle in ("一元二次方程", "数学", "九年级", "AI辅助生成"):
        assert needle in cover, needle

    for index, scene in enumerate(COURSEWARE["scenes"], start=1):
        slide = prs.slides[index]
        text = _slide_text(slide)
        assert scene["title"] in text
        for bullet in scene["bullets"]:
            assert bullet in text
        assert slide.notes_slide.notes_text_frame.text == scene["narration"]


def test_build_pptx_skips_empty_fields():
    prs = Presentation(io.BytesIO(build_pptx({"scenes": [{"title": "仅标题"}]})))
    assert len(prs.slides) == 2
    assert "仅标题" in _slide_text(prs.slides[1])
    assert prs.slides[1].notes_slide.notes_text_frame.text == ""


# --- endpoint -----------------------------------------------------------------


def test_export_does_not_charge(db, client):
    session, users = db
    job = _make_job(db, users["teacher"])
    _login(client, users["teacher"])

    before_entries = session.query(CreditEntry).count()
    before_balance = session.query(CreditBalance).filter_by(
        user_id=users["teacher"].id, pool="universal"
    ).one().balance

    urls = f"/api/export/{job.id}.pptx"
    first = client.get(urls)
    second = client.get(urls)
    assert first.status_code == 200, first.text[:200]
    assert second.status_code == 200, second.text[:200]
    assert first.headers["content-type"] == PPTX_MEDIA_TYPE
    assert 'attachment; filename="courseware.pptx"' in first.headers[
        "content-disposition"
    ]
    assert Presentation(io.BytesIO(first.content)).slides
    assert first.content == second.content  # deterministic replay

    # No export charge: no new ledger rows at all, balance untouched.
    assert session.query(CreditEntry).filter_by(ref_type="export").count() == 0
    assert session.query(CreditEntry).count() == before_entries
    assert session.query(CreditBalance).filter_by(
        user_id=users["teacher"].id, pool="universal"
    ).one().balance == before_balance


def test_export_requires_owner(db, client):
    session, users = db
    job = _make_job(db, users["teacher"])

    _login(client, users["other"])
    denied = client.get(f"/api/export/{job.id}.pptx")
    assert denied.status_code in (403, 404)

    # An admin may export another teacher's courseware.
    _login(client, users["admin"])
    allowed = client.get(f"/api/export/{job.id}.pptx")
    assert allowed.status_code == 200, allowed.text[:200]


def test_export_without_courseware_is_409(db, client):
    _, users = db
    job = _make_job(db, users["teacher"], result=None)
    _login(client, users["teacher"])
    assert client.get(f"/api/export/{job.id}.pptx").status_code in (404, 409)


def test_export_handles_result_without_scenes(db, client):
    """The mock adapter's finished artifact yields a cover-only deck, not 409."""
    _, users = db
    job = _make_job(
        db, users["teacher"], result={"share_code": "S1", "credits": 10}
    )
    _login(client, users["teacher"])
    response = client.get(f"/api/export/{job.id}.pptx")
    assert response.status_code == 200
    assert len(Presentation(io.BytesIO(response.content)).slides) == 1


def test_classroom_edit_has_free_pptx_download_link(db, client):
    _, users = db
    job = _make_job(db, users["teacher"])
    _login(client, users["teacher"])
    page = client.get(f"/classroom/{job.id}/edit")
    assert page.status_code == 200
    assert f"/api/export/{job.id}.pptx" in page.text
    assert "下载 PPT" in page.text
