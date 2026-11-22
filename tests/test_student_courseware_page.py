"""N5: student classroom renders the self-owned Courseware (real shape).

TDD for the two product-level defects found by real-data validation:
  1. scenes use ``narration`` + ``bullets`` (no ``body``) but the JS only read
     ``s.body`` -> students saw a placeholder and lost all teaching content;
  2. quiz only listed ``question`` text -- no ``options`` / ``answer`` /
     ``explain`` interaction.

All data here is hand-written fake/seed shaped like a REAL Courseware; no real
API call and no key. The JS behaviour is verified with static assertions on the
rendered HTML (native JS, no build toolchain), plus a browser-free compat case.
"""
from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import GenerationJob, User


REAL_SHARE_CODE = "CREAL5"
STR_SHARE_CODE = "CSTR5"


def _real_courseware() -> dict:
    """A Courseware with the exact field shape a real DeepSeek run produces."""
    return {
        "version": "1.0",
        "title": "一元二次方程的配方法",
        "subject": "数学",
        "grade": "九年级",
        "scenes": [
            {
                "type": "slide",
                "title": "第一页标题：什么是配方法",
                "bullets": [
                    "把 ax²+bx+c=0 的左边凑成完全平方式",
                    "转化为 (x+m)²=n 求解",
                ],
                "narration": "配方法的核心是把左边加减同一个数凑成完全平方式，再写成完全平方求解。",
                "visual_hint": "白板演示：x²+6x+5=0 → (x+3)²=4",
            },
            {
                "type": "slide",
                "title": "第二页标题：五个步骤",
                "bullets": ["二次项系数化为1", "移项", "两边加一次项系数一半的平方"],
                "narration": "记住五步：化系数、移项、加平方、写完全平方、开平方求解。",
                "visual_hint": "",
            },
        ],
        "quiz": [
            {
                "question": "用配方法解 x²+6x+5=0，配方后正确的是？",
                "options": ["(x+3)²=4", "(x-3)²=4", "(x+6)²=5", "(x+3)²=5"],
                "answer": 0,
                "explain": "x²+6x+5=0 → x²+6x=-5 → 两边加9 → (x+3)²=4。",
            },
            {
                "question": "配方法的第一步通常是？",
                "options": ["直接开平方", "把二次项系数化为1", "移项", "写出答案"],
                "answer": 1,
                "explain": "当二次项系数不为 1 时，先两边同除以二次项系数。",
            },
        ],
        "aigc": {"label": "AI辅助生成", "provider": "deepseek", "model": "deepseek-chat"},
    }


@contextmanager
def serving(result: dict, share_code: str):
    """Serve a job whose ``result`` is the given Courseware through /c/{code}."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    teacher = User(
        phone="13900000009",
        display_name="N5老师",
        role="user",
        is_active=True,
        invite_code="N5TEACH",
    )
    db.add(teacher)
    db.flush()
    job = GenerationJob(
        user_id=teacher.id,
        spec={"topic": "一元二次方程的配方法", "type": "interactive"},
        status="succeeded",
        result={**result, "share_code": share_code, "share_url": f"/c/{share_code}"},
        idempotency_key=f"n5-{share_code}",
    )
    db.add(job)
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


# --- N5 defect 1: narration + bullets must be visible -------------------------


def test_student_page_renders_narration_and_bullets():
    """Real-shaped scenes must show narration/bullets/visual_hint, not a placeholder."""
    cw = _real_courseware()
    scene = cw["scenes"][0]
    with serving(cw, REAL_SHARE_CODE) as client:
        r = client.get(f"/c/{REAL_SHARE_CODE}")
    assert r.status_code == 200, r.text[:200]
    html = r.text

    # The generated content reaches the page (server-rendered + tojson).
    assert scene["title"] in html
    assert scene["narration"] in html
    for bullet in scene["bullets"]:
        assert bullet in html
    assert scene["visual_hint"] in html

    # The JS actually renders those fields, with narration preferred over the
    # legacy ``body`` field -- this is the line that was missing (defect 1).
    assert "s.narration" in html
    assert "s.narration || s.body" in html
    assert "s.bullets" in html
    assert "s.visual_hint" in html


# --- N5 defect 2: quiz must be answerable ------------------------------------


def test_quiz_renders_options_and_answer_logic():
    """Options are clickable; answer/explain shown; progress updated dynamically."""
    cw = _real_courseware()
    q = cw["quiz"][0]
    with serving(cw, REAL_SHARE_CODE) as client:
        r = client.get(f"/c/{REAL_SHARE_CODE}")
    assert r.status_code == 200, r.text[:200]
    html = r.text

    # question, every option and the explanation are present in the page.
    assert q["question"] in html
    for option in q["options"]:
        assert option in html
    assert q["explain"] in html
    assert 'class="btn quiz-option"' in html

    # The JS compares the selected index with q.answer, shows ✓/✗ and explains,
    # and updates the quiz-progress counter instead of leaving a static 0/N.
    assert "q.answer" in html
    assert "q.explain" in html
    assert "✓" in html and "✗" in html
    assert "updateQuizProgress" in html
    assert "quiz-progress" in html


# --- backward compatibility: old string-shaped mock still works --------------


def test_string_scene_and_quiz_compat():
    """Legacy string scenes/quiz keep rendering (the existing mock shape)."""
    result = {
        "title": "旧版模拟课件",
        "scenes": ["纯字符串场景一", "纯字符串场景二"],
        "quiz": ["字符串题目一"],
    }
    with serving(result, STR_SHARE_CODE) as client:
        r = client.get(f"/c/{STR_SHARE_CODE}")
    assert r.status_code == 200, r.text[:200]
    html = r.text
    assert "纯字符串场景一" in html
    assert "字符串题目一" in html
    assert "typeof s === 'string'" in html
    assert "typeof q === 'string'" in html
