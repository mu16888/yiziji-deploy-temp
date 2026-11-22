"""Tests for the generation pipeline: reserve -> run -> settle/refund -> share (Task 7).

Money flow is asserted with verbatim balances from the brief. The mock adapter
follows design §12.6; a deterministic in-test adapter supplies the brief's exact
reserve figure of 20 so the reserve/settle/refund deltas are asserted verbatim.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.adapters.generation import GenerationAdapter
from app.adapters.generation.mock import MockGenerationAdapter
from app.adapters.generation.openmaic_http import OpenMaicHttpAdapter
from app.db import Base
from app.domain.generation import (
    FAILED,
    QUEUED,
    SUCCEEDED,
    InsufficientCreditsForJob,
    InvalidJobState,
    complete_job,
    create_job,
    fail_job,
    get_job,
    submit_job,
)
from app.domain.ledger import append_entry
from app.models import CreditEntry, GenerationJob
from app.services.credits import get_balance
from app.workers.worker import poll_once


# --- fixtures / helpers -------------------------------------------------------


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'generation.db'}")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    yield session
    session.close()


def _grant(db, uid, delta, key):
    append_entry(
        db,
        user_id=uid,
        delta=delta,
        entry_type="grant",
        ref_type="signup",
        ref_id=str(uid),
        idempotency_key=key,
    )
    db.commit()


class FakeAdapter:
    """Deterministic adapter used to pin exact credit amounts in money tests."""

    def __init__(self, estimate=20, status="succeeded", result=None):
        self.estimate_value = estimate
        self.status_value = status
        self.result_value = result or {"share_code": "S123", "credits": estimate}
        self.submitted: list[tuple[int, dict]] = []
        self.cancelled: list[str] = []

    def estimate(self, spec: dict) -> int:
        return self.estimate_value

    def submit(self, job, spec: dict) -> str:
        self.submitted.append((job.id, dict(spec)))
        return f"ext-{job.id}"

    def status(self, external_id: str) -> str:
        return self.status_value

    def cancel(self, external_id: str) -> None:
        self.cancelled.append(external_id)

    def result(self, external_id: str) -> dict:
        return dict(self.result_value)


# --- brief tests --------------------------------------------------------------


def test_job_reserve_then_settle_with_refund_difference(db):
    """Verbatim: balance 100 -> reserve 20 -> 80; complete actual 15 -> 85."""
    _grant(db, 1, 100, "signup-grant:1")
    fake = FakeAdapter(estimate=20)

    j = create_job(
        db,
        user_id=1,
        spec={"type": "interactive_classroom", "scenes": 12},
        adapter=fake,
    )
    assert j.status == QUEUED
    assert j.reserved_credits == 20
    assert get_balance(db, 1) == 80  # 预扣 20

    done = complete_job(db, job_id=j.id, actual_credits=15, result={"share_code": "S123"})
    assert done.status == SUCCEEDED
    assert done.actual_credits == 15
    assert get_balance(db, 1) == 85  # 退回 5


def test_failed_job_refunds_full_reservation(db):
    """A failed job returns the entire reservation; retry stays at 100."""
    _grant(db, 1, 100, "signup-grant:1")
    j = create_job(db, user_id=1, spec={"type": "pbl"}, adapter=FakeAdapter(estimate=20))
    assert get_balance(db, 1) == 80

    j = fail_job(db, job_id=j.id)
    assert j.status == FAILED
    assert get_balance(db, 1) == 100

    # replay is a no-op: one refund row, balance unchanged
    fail_job(db, job_id=j.id)
    assert get_balance(db, 1) == 100
    assert (
        db.query(CreditEntry)
        .filter_by(idempotency_key=f"refund:{j.id}")
        .count()
        == 1
    )


def test_mock_adapter_generates_share_code(db):
    """The real mock adapter follows §12.6 and produces a /c/{share_code} link."""
    _grant(db, 1, 100, "signup-grant:1")
    adapter = MockGenerationAdapter()

    # §12.6 flat estimation rules
    assert adapter.estimate({"type": "interactive_classroom"}) == 10
    assert adapter.estimate({"type": "interactive_classroom", "quiz": True}) == 25
    assert adapter.estimate({"type": "pbl"}) == 40
    assert adapter.estimate({"type": "export", "format": "pptx"}) == 5

    job = create_job(
        db, user_id=1, spec={"type": "interactive_classroom", "quiz": True}, adapter=adapter
    )
    external_id = adapter.submit(job, job.spec)
    assert isinstance(external_id, str) and external_id
    assert adapter.status(external_id) == SUCCEEDED

    result = adapter.result(external_id)
    assert result["share_code"]
    assert result["share_url"] == f"/c/{result['share_code']}"


def test_mock_estimate_ui_tier_types():
    """Final review Important #5: the demo UI's ``type`` values hit §12.6 tiers.

    generate.html submits type=interactive/quiz/pbl/basic; the mock estimator
    must price 25/25/40/10 respectively (it used to fall through to 10).
    """
    adapter = MockGenerationAdapter()
    assert adapter.estimate({"type": "interactive"}) == 25
    assert adapter.estimate({"type": "quiz"}) == 25
    assert adapter.estimate({"type": "interactive_classroom_with_quiz"}) == 25
    assert adapter.estimate({"type": "pbl"}) == 40
    assert adapter.estimate({"type": "basic"}) == 10
    # Legacy flag-keyed specs keep working.
    assert adapter.estimate({"type": "basic", "has_quiz": True}) == 25


# --- worker / end-to-end ------------------------------------------------------


def test_queued_job_becomes_succeeded_via_poll(db):
    """One poll submits a queued job and settles it (mock status is immediate)."""
    _grant(db, 1, 100, "signup-grant:1")
    adapter = MockGenerationAdapter()
    j = create_job(
        db, user_id=1, spec={"type": "interactive_classroom"}, adapter=adapter
    )
    assert j.status == QUEUED
    assert j.reserved_credits == 10

    advanced = poll_once(db, adapter=adapter)
    assert advanced >= 1

    done = get_job(db, j.id)
    assert done.status == SUCCEEDED
    assert done.external_id
    assert done.result and "share_code" in done.result
    assert get_balance(db, 1) == 90  # reserved 10, actual 10 -> no diff


def test_queued_job_becomes_failed_via_poll_with_full_refund(db):
    """The worker maps an engine 'failed' status to fail_job (full refund)."""
    _grant(db, 1, 100, "signup-grant:1")
    adapter = MockGenerationAdapter()
    j = create_job(db, user_id=1, spec={"type": "pbl"}, adapter=adapter)  # 40 credits
    assert get_balance(db, 1) == 60

    submit_job(db, job_id=j.id, adapter=adapter)
    adapter.mark_failed(j.external_id)

    assert poll_once(db, adapter=adapter) >= 1
    done = get_job(db, j.id)
    assert done.status == FAILED
    assert get_balance(db, 1) == 100


def test_settle_charges_extra_when_actual_exceeds_estimate(db):
    """多退少补 in the other direction: actual > reserved charges the difference."""
    _grant(db, 1, 100, "signup-grant:1")
    j = create_job(db, user_id=1, spec={"type": "pbl"}, adapter=FakeAdapter(estimate=20))
    complete_job(db, job_id=j.id, actual_credits=30, result={"share_code": "S-X"})
    assert get_balance(db, 1) == 70  # -20 reserve, -10 extra


def test_terminal_job_cannot_be_completed_twice(db):
    _grant(db, 1, 100, "signup-grant:1")
    j = create_job(db, user_id=1, spec={"type": "pbl"}, adapter=FakeAdapter(estimate=20))
    complete_job(db, job_id=j.id, actual_credits=15, result={"share_code": "S1"})
    with pytest.raises(InvalidJobState):
        complete_job(db, job_id=j.id, actual_credits=15, result={"share_code": "S1"})
    with pytest.raises(InvalidJobState):
        fail_job(db, job_id=j.id)
    assert get_balance(db, 1) == 85


def test_create_job_insufficient_credits_rolls_back(db):
    _grant(db, 1, 5, "signup-grant:1")
    with pytest.raises(InsufficientCreditsForJob):
        create_job(db, user_id=1, spec={"type": "pbl"}, adapter=FakeAdapter(estimate=20))
    assert db.query(GenerationJob).count() == 0
    assert get_balance(db, 1) == 5


def test_openmaic_http_adapter_uses_configured_url():
    """The HTTP adapter calls OPENMAIC_URL with the same Protocol surface."""

    class _Resp:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    class _Client:
        def __init__(self):
            self.calls = []

        def post(self, url, json=None, **kwargs):
            self.calls.append(("POST", url, json))
            if url.endswith("/estimate"):
                return _Resp({"credits": 25})
            if url.endswith("/jobs"):
                return _Resp({"external_id": "om-1"})
            return _Resp({})

        def get(self, url, **kwargs):
            self.calls.append(("GET", url, None))
            if "/status" in url:
                return _Resp({"status": "succeeded"})
            if "/result" in url:
                return _Resp({"share_code": "S-OM", "credits": 25})
            return _Resp({})

    client = _Client()
    adapter = OpenMaicHttpAdapter(base_url="https://om.example/api", client=client)
    assert isinstance(adapter, GenerationAdapter)
    assert adapter.estimate({"type": "pbl"}) == 25
    assert (
        adapter.submit(SimpleNamespace(id=7, reserved_credits=25, spec={}), {}) == "om-1"
    )
    assert adapter.status("om-1") == SUCCEEDED
    assert adapter.result("om-1")["share_code"] == "S-OM"
    assert all(url.startswith("https://om.example/api") for _, url, _ in client.calls)
    adapter.cancel("om-1")
