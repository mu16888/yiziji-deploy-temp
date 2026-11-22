"""Tests for the self-owned DeepSeek generation adapter (N4).

Fake LLMs are injected everywhere: no real API call and no key ever appears in
code or tests. The Review Focus cases from the plan are covered verbatim, plus
the worker contract (submit -> status -> result -> settle/refund) and adapter
registration via ``get_adapter``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.adapters.generation import GenerationAdapter, get_adapter
from app.adapters.generation.deepseek import DeepSeekGenerationAdapter
from app.domain.courseware import SCHEMA_VERSION, CoursewareError


# --- fakes ---------------------------------------------------------------------


class FakeLLM:
    """Return a fixed JSON object keyed off the orchestration system message."""

    provider = "deepseek"
    model = "fake-model"

    def __init__(self, *, fail_on: str | None = None):
        self.calls: list[tuple[str, str]] = []
        self.fail_on = fail_on

    def complete_json(self, system, user, *, temperature=0.7):
        self.calls.append((system, user))
        combined = f"{system}\n{user}"
        if self.fail_on and self.fail_on in combined:
            raise CoursewareError(f"forced failure in: {system}")
        if "资深初中数学老师" in system:
            return {
                "title": "一元二次方程",
                "subject": "数学",
                "grade": "九年级",
                "points": ["配方法", "求根公式"],
            }
        if "设计一节课的场景" in system:
            return {"scenes": [{"title": "引入", "bullets": ["什么是方程"]}]}
        if "撰写一个教学场景" in system:
            return {
                "type": "slide",
                "title": "引入",
                "bullets": ["什么是方程"],
                "narration": "今天我们学习一元二次方程。",
                "visual_hint": "板书标题",
            }
        if "出随堂测验" in system:
            base = {
                "options": ["x+1=0", "x^2+1=0", "1/x=1", "2x=4"],
                "answer": 1,
                "explain": "最高次数为 2。",
            }
            return {
                "quiz": [
                    {**base, "question": "下面哪个是一元二次方程？"},
                    {**base, "question": "配方法先把二次项系数化为几？"},
                    {**base, "question": "判别式 Δ 决定什么？"},
                ]
            }
        raise AssertionError(f"unexpected prompt: {combined}")


class CreditsLyingLLM(FakeLLM):
    """A model that pretends to price the job (must be ignored by estimate)."""

    def score(self) -> int:  # merely here to make the point explicit
        return 999

    def complete_json(self, system, user, *, temperature=0.7):
        raise AssertionError("estimate() must never call the LLM")


def make_job(job_id=7, reserved=10):
    return SimpleNamespace(id=job_id, reserved_credits=reserved, spec={})


# --- Review Focus ---------------------------------------------------------------


def test_estimate_ignores_llm():
    """A model returning credits=999 does not affect estimate (local rules only)."""
    adapter = DeepSeekGenerationAdapter(client=CreditsLyingLLM())
    assert adapter.estimate({"type": "basic"}) == 10


def test_submit_produces_courseware():
    """submit -> status succeeded -> result carries version + share_code."""
    llm = FakeLLM()
    adapter = DeepSeekGenerationAdapter(client=llm, store={})
    eid = adapter.submit(make_job(), {"text": "一元二次方程讲义", "type": "basic"})
    assert eid == "ds-7"
    assert adapter.status(eid) == "succeeded"
    result = adapter.result(eid)
    assert result["version"] == SCHEMA_VERSION == "1.0"
    assert result["share_code"] == "D000007"
    assert result["share_url"] == "/c/D000007"
    assert result["scenes"] and result["quiz"]
    assert llm.calls  # the orchestration actually ran


def test_failed_orchestration_refunds():
    """A fatal CoursewareError is recorded as failed so the worker refunds."""
    llm = FakeLLM(fail_on="提取主题")  # understanding phase is fatal
    adapter = DeepSeekGenerationAdapter(client=llm, store={})
    eid = adapter.submit(make_job(), {"text": "讲义", "type": "basic"})
    assert eid == "ds-7"
    assert adapter.status(eid) == "failed"  # worker -> fail_job -> refund


# --- §12.6 local estimate rules -------------------------------------------------


def test_estimate_matches_mock_rules():
    a = DeepSeekGenerationAdapter(client=CreditsLyingLLM())
    assert a.estimate({"type": "basic"}) == 10
    assert a.estimate({"type": "interactive"}) == 25
    assert a.estimate({"type": "quiz"}) == 25
    assert a.estimate({"type": "interactive_classroom_with_quiz"}) == 25
    assert a.estimate({"type": "pbl"}) == 40
    assert a.estimate({"type": "export", "format": "pptx"}) == 5
    assert a.estimate({"type": "basic", "has_quiz": True}) == 25


# --- registry lifecycle ---------------------------------------------------------


def test_cancel_marks_cancelled():
    adapter = DeepSeekGenerationAdapter(client=FakeLLM(), store={})
    eid = adapter.submit(make_job(), {"text": "讲义"})
    assert adapter.status(eid) == "succeeded"
    adapter.cancel(eid)
    assert adapter.status(eid) == "cancelled"


def test_material_text_takes_priority_over_text():
    """Either spec key is accepted; material_text wins when both are present."""
    llm = FakeLLM()
    adapter = DeepSeekGenerationAdapter(client=llm, store={})
    eid = adapter.submit(
        make_job(), {"text": "忽略", "material_text": "真正的讲义", "type": "basic"}
    )
    assert adapter.status(eid) == "succeeded"
    # The chosen text actually reached the orchestration, not just the "text" key.
    assert llm.calls and any("真正的讲义" in user for _, user in llm.calls)
    assert all("忽略" not in user for _, user in llm.calls)


def test_default_text_when_spec_has_no_text():
    """A missing text still produces a demo courseware (no KeyError)."""
    adapter = DeepSeekGenerationAdapter(client=FakeLLM(), store={})
    eid = adapter.submit(make_job(), {"type": "basic"})
    assert adapter.status(eid) == "succeeded"


def test_injected_store_is_used():
    store = {}
    adapter = DeepSeekGenerationAdapter(client=FakeLLM(), store=store)
    eid = adapter.submit(make_job(), {"text": "讲义"})
    assert eid in store


# --- unknown external id (review fix: never settle an empty artifact) -----------


def test_unknown_eid_reports_running_and_has_no_result():
    """A missing registry entry must not look like a finished job."""
    adapter = DeepSeekGenerationAdapter(client=FakeLLM(), store={})
    assert adapter.status("ds-unknown") == "running"
    assert adapter.status("ds-unknown") != "succeeded"
    assert adapter.result("ds-unknown") is None


# --- registration ---------------------------------------------------------------


def test_get_adapter_deepseek(monkeypatch):
    from app.config import settings

    adapter = get_adapter("deepseek")
    assert isinstance(adapter, DeepSeekGenerationAdapter)
    assert isinstance(adapter, GenerationAdapter)

    monkeypatch.setattr(settings, "generation_adapter", "deepseek")
    assert isinstance(get_adapter(), DeepSeekGenerationAdapter)


# --- worker contract end-to-end -------------------------------------------------


@pytest.fixture()
def db(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.domain.ledger import append_entry

    eng = create_engine(f"sqlite:///{tmp_path / 'deepseek-adapter.db'}")
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng, autoflush=False, autocommit=False)()
    append_entry(
        session,
        user_id=1,
        delta=100,
        entry_type="grant",
        ref_type="signup",
        ref_id="1",
        idempotency_key="signup-grant:1",
    )
    session.commit()
    yield session
    session.close()
    eng.dispose()


def test_worker_settles_successful_courseware(db):
    """One poll: submit -> succeeded -> result carries share_code -> settle."""
    from app.domain.generation import SUCCEEDED, create_job
    from app.services.credits import get_balance
    from app.workers.worker import poll_once

    adapter = DeepSeekGenerationAdapter(client=FakeLLM(), store={})
    job = create_job(db, user_id=1, spec={"type": "basic", "text": "讲义"}, adapter=adapter)
    assert job.reserved_credits == 10

    assert poll_once(db, adapter=adapter) >= 1
    done = db.get(type(job), job.id)
    assert done.status == SUCCEEDED
    assert done.external_id == f"ds-{job.id}"
    assert done.result["share_code"] == f"D{job.id:06d}"
    assert get_balance(db, 1) == 90


def test_worker_refunds_failed_orchestration(db):
    """Fatal orchestration failure -> status failed -> fail_job refunds credits."""
    from app.domain.generation import FAILED, create_job
    from app.services.credits import get_balance
    from app.workers.worker import poll_once

    adapter = DeepSeekGenerationAdapter(
        client=FakeLLM(fail_on="提取主题"), store={}
    )
    job = create_job(db, user_id=1, spec={"type": "pbl", "text": "讲义"}, adapter=adapter)
    assert job.reserved_credits == 40
    assert get_balance(db, 1) == 60

    assert poll_once(db, adapter=adapter) >= 1
    done = db.get(type(job), job.id)
    assert done.status == FAILED
    assert get_balance(db, 1) == 100  # full refund


def test_unknown_eid_never_settles(db):
    """A worker restart with a fresh store must not settle a running job.

    With the old `status()` default of "succeeded", `poll_once` would call
    `_fetch_result`, get an empty dict from `result()`, and `complete_job`
    would charge the customer while marking the job SUCCEEDED with no artifact.
    """
    from app.domain.generation import RUNNING, create_job
    from app.services.credits import get_balance
    from app.workers.worker import poll_once

    adapter = DeepSeekGenerationAdapter(client=FakeLLM(), store={})
    job = create_job(db, user_id=1, spec={"type": "basic", "text": "讲义"}, adapter=adapter)
    assert job.reserved_credits == 10
    assert get_balance(db, 1) == 90

    # Simulate a job left RUNNING by a previous worker whose eid is absent from
    # this adapter's store (worker restart / multi-worker sharing one DB).
    job.status = RUNNING
    job.external_id = "ds-lost-after-restart"
    db.commit()

    assert poll_once(db, adapter=adapter) == 0  # no queued/running work advanced
    done = db.get(type(job), job.id)
    assert done.status == RUNNING
    assert done.result is None
    assert get_balance(db, 1) == 90  # no premature charge
