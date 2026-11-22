"""N6: end-to-end self-owned generation loop -- fake LLM, no key, no network.

The full product story of 「你自己的课堂」:

    注册 → 备课(estimate/reserve) → DeepSeekGenerationAdapter(fake LLM)
    → poll_once(submit + settle) → 合法 Courseware(v1.0, scenes, quiz)
    → 分享码 → 学生页 /c/{share_code} 200 渲染旁白与选项 → 积分结算正确

The LLM is injected as a fake, so no key and no real API are involved. This
test also gates the shipped documentation: the rejected OpenMAIC direction must
be gone and the replaceable DeepSeek backend must be documented.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.generation.deepseek import DeepSeekGenerationAdapter
from app.db import Base, get_db
from app.domain.courseware import (
    AIGC_LABEL,
    QUIZ_SYSTEM,
    SCENE_PLAN_SYSTEM,
    SCENE_SYSTEM,
    SCHEMA_VERSION,
    UNDERSTANDING_SYSTEM,
)
from app.main import app
from app.models import GenerationJob, User
from app.services.auth import issue_token
from app.services.credits import get_balance
from app.workers.worker import poll_once

CODE = "123456"  # VERIFY_CODE_DEV
LECTURE = (
    "本节课学习一元二次方程。形如 ax²+bx+c=0（a≠0）的方程叫做一元二次方程。"
    "解法包括配方法、公式法和因式分解法。"
)


# --- fake LLM (no key, no network) --------------------------------------------


class E2EFakeLLM:
    """Return the orchestration JSON keyed off the validated system messages."""

    provider = "deepseek"
    model = "fake-model"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete_json(self, system, user, *, temperature=0.7):
        self.calls.append(system)
        if system == UNDERSTANDING_SYSTEM:
            return {
                "title": "一元二次方程",
                "subject": "数学",
                "grade": "九年级",
                "points": ["配方法", "求根公式"],
            }
        if system == SCENE_PLAN_SYSTEM:
            return {"scenes": [{"title": "引入", "bullets": ["什么是方程"]}]}
        if system == SCENE_SYSTEM:
            return {
                "type": "slide",
                "title": "引入",
                "bullets": ["什么是方程"],
                "narration": "今天我们学习一元二次方程。",
                "visual_hint": "板书标题",
            }
        if system == QUIZ_SYSTEM:
            return {
                "quiz": [
                    {
                        "question": "下面哪个是一元二次方程？",
                        "options": ["x+1=0", "x^2+1=0", "1/x=1", "2x=4"],
                        "answer": 1,
                        "explain": "最高次数为 2。",
                    }
                ]
            }
        raise AssertionError(f"unexpected system prompt: {system}")


# --- fixtures ------------------------------------------------------------------


@pytest.fixture()
def llm() -> E2EFakeLLM:
    return E2EFakeLLM()


@pytest.fixture()
def adapter(llm: E2EFakeLLM) -> DeepSeekGenerationAdapter:
    return DeepSeekGenerationAdapter(client=llm, store={})


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
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db, adapter: DeepSeekGenerationAdapter, monkeypatch):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    # Inject the same fake-LLM adapter into every consumer of the pipeline:
    # the API (estimate/create), the domain layer and the worker.
    for target in (
        "app.api.jobs.get_adapter",
        "app.domain.generation.get_adapter",
        "app.workers.worker.get_adapter",
    ):
        monkeypatch.setattr(target, lambda *a, **k: adapter)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _auth(client: TestClient, db, user_id: int) -> dict[str, str]:
    """Bearer headers for one actor; clear cookies so the Bearer channel wins."""
    client.cookies.clear()
    user = db.get(User, user_id)
    assert user is not None
    return {"Authorization": f"Bearer {issue_token(user, channel='api')}"}


# --- the loop ------------------------------------------------------------------


def test_e2e_own_generation_loop(db, client, adapter, llm):
    # 0. 健康检查
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}

    # 1. 注册：送 100 积分
    r = client.post(
        "/api/auth/register",
        json={"phone": "13822220001", "code": CODE},
    )
    assert r.status_code == 200, r.text
    teacher_id = r.json()["user_id"]
    teacher_headers = _auth(client, db, teacher_id)
    assert get_balance(db, teacher_id) == 100

    # 2. 备课估算：DeepSeek 适配器的估算只走本地规则，绝不调用 LLM
    spec = {"type": "basic", "text": LECTURE}
    est = client.post("/api/estimate", json={"spec": spec}, headers=teacher_headers)
    assert est.status_code == 200, est.text
    assert est.json()["credits"] == 10
    assert llm.calls == []  # estimate is a local decision

    # 3. 建任务：预扣 10 积分，状态 queued
    r = client.post("/api/jobs", json={"spec": spec}, headers=teacher_headers)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "queued" and job["reserved_credits"] == 10
    assert get_balance(db, teacher_id) == 90

    # 4. worker 一轮：submit → 自研编排 → settle（同一轮完成）
    assert poll_once(db, adapter=adapter) >= 1
    done = db.get(GenerationJob, job["id"])
    assert done.status == "succeeded"
    assert done.external_id == f"ds-{job['id']}"
    assert llm.calls  # 编排确实跑过

    # 5. 结果是合法的 Courseware：版本、场景、测验齐全
    result = done.result
    assert result["version"] == SCHEMA_VERSION == "1.0"
    assert result["scenes"] and result["quiz"]
    scene, quiz = result["scenes"][0], result["quiz"][0]
    assert scene["narration"] and scene["bullets"]
    assert quiz["options"] and quiz["question"] and quiz["explain"]
    assert result["aigc"]["label"] == AIGC_LABEL
    share_code = result["share_code"]
    assert share_code == f"D{job['id']:06d}"
    assert result["share_url"] == f"/c/{share_code}"

    # 6. 积分结算正确：预估=实耗=10，余额保持 90，不多退少补
    assert done.actual_credits == 10
    assert get_balance(db, teacher_id) == 90

    # 7. 学生页：免登录 GET /c/{share_code} 200，渲染旁白与测验选项
    client.cookies.clear()
    r = client.get(f"/c/{share_code}")
    assert r.status_code == 200, r.text[:300]
    html = r.text
    assert scene["narration"] in html
    for option in quiz["options"]:
        assert option in html
    assert quiz["question"] in html
    assert "本内容由 AI 生成" in html  # AIGC_LABEL 默认开启
    # 原生 JS 真实读取生成的内容字段（非占位）
    assert "s.narration || s.body" in html
    assert "q.answer" in html
    assert "q.explain" in html


# --- shipped docs / brand acceptance -------------------------------------------


def test_shipped_docs_are_own_generation_and_brand_clean():
    """README and .env.example must describe the own-generation product only."""
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")

    required = (
        "自有生成",
        "Courseware",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_MODEL",
        "GENERATION_ADAPTER",
        "AIGC_LABEL",
        ".env.deepseek.howto.md",
    )
    for token in required:
        assert token in readme, f"README missing own-generation topic: {token}"

    banned = ("openmaic", "清华", "thu", "lgpl", "mathml2omml", "pptxgenjs")
    lowered = readme.lower()
    for token in banned:
        assert token not in lowered, f"README still contains rejected reference: {token}"

    env_example = (root / ".env.example").read_text(encoding="utf-8")
    for token in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"):
        assert token in env_example, f".env.example missing: {token}"
    assert "OPENMAIC" not in env_example.upper()
