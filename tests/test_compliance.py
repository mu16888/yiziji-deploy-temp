"""Tests for AIGC compliance marking and audit retention (Task 11).

The environment defaults are verbatim from the brief: AIGC_LABEL=true and
AUDIT_RETENTION_DAYS=190. Marking is metadata-only (no media binary stamping).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.domain.generation import complete_job, create_job
from app.domain.ledger import append_entry
from app.models import AigcAudit, GenerationJob
from app.models.base import utcnow
from app.services import compliance
from app.services.compliance import mark_aigc
from scripts.purge_logs import main as purge_logs_main


# --- fixtures -----------------------------------------------------------------


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'compliance.db'}")
    from app.db import Base

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    yield session
    session.close()


class FakeAdapter:
    """Minimal adapter: no external engine, result supplied by the test."""

    def estimate(self, spec: dict) -> int:
        return 10

    def submit(self, job, spec: dict) -> str:
        return f"ext-{job.id}"

    def status(self, external_id: str) -> str:
        return "succeeded"

    def cancel(self, external_id: str) -> None:
        pass

    def result(self, external_id: str) -> dict:
        return {"share_code": "S-AIGC"}


# --- AIGC labeling ------------------------------------------------------------


def test_export_carries_aigc_label():
    out = mark_aigc({"format": "pptx"})
    assert out["aigc_label"] is True and "AIGC" in out["meta"]


def test_mark_aigc_includes_video_start_label_text():
    out = mark_aigc({"format": "pptx"})
    assert out["meta"]["AIGC"] == "AI辅助生成"


def test_mark_aigc_preserves_result_and_is_idempotent():
    original = {"share_code": "S1", "meta": {"source": "mock"}}
    out = mark_aigc(original)
    out = mark_aigc(out)  # T7 marks on settle, T10 marks again on render

    assert out["share_code"] == "S1"
    assert original == {"share_code": "S1", "meta": {"source": "mock"}}
    assert out["meta"]["source"] == "mock"
    assert sum(1 for k in out["meta"] if k == "AIGC") == 1


def test_mark_aigc_respects_disabled_flag(monkeypatch):
    monkeypatch.setattr(settings, "aigc_label", False)
    out = mark_aigc({"format": "html"})
    assert out["aigc_label"] is False
    assert "AIGC" not in out.get("meta", {})


# --- audit recording ----------------------------------------------------------


def test_record_audit_persists_row(db):
    row = compliance.record_audit(
        db,
        actor_id=7,
        action="aigc:generate",
        payload={"share_code": "S1", "at": utcnow()},
    )
    db.commit()

    stored = db.query(AigcAudit).one()
    assert stored.id == row.id
    assert stored.actor_id == 7
    assert stored.action == "aigc:generate"
    assert stored.aigc_label is True
    assert stored.payload["share_code"] == "S1"
    assert isinstance(stored.payload["at"], str)  # datetime -> ISO for JSON column


def test_generation_completion_records_aigc_audit(db):
    append_entry(db, user_id=1, delta=100, entry_type="grant",
                 ref_type="signup", ref_id="1", idempotency_key="signup-grant:1")
    db.commit()
    job = create_job(db, user_id=1, spec={"type": "pbl"}, adapter=FakeAdapter())
    complete_job(db, job_id=job.id, actual_credits=10, result={"share_code": "S-AIGC"})

    row = db.query(AigcAudit).one()
    assert row.action == "aigc:generate"
    assert row.job_id == job.id
    assert row.actor_id == 1
    assert row.payload["aigc_label"] is True

    done = db.query(GenerationJob).one()
    assert done.result["aigc_label"] is True


# --- retention / CLI ----------------------------------------------------------


def test_audit_retention(db):
    retention = settings.audit_retention_days  # verbatim default: 190
    expired = compliance.record_audit(
        db, actor_id=1, action="expired", payload={"k": "old"}
    )
    kept = compliance.record_audit(
        db, actor_id=1, action="fresh", payload={"k": "new"}
    )
    expired.created_at = utcnow() - timedelta(days=retention + 1)
    kept.created_at = utcnow() - timedelta(days=retention - 1)
    db.commit()

    removed = compliance.purge_expired(db)

    assert removed == 1
    assert [r.action for r in db.query(AigcAudit).all()] == ["fresh"]


def test_purge_logs_cli_cleans_expired_rows(tmp_path):
    url = f"sqlite:///{tmp_path / 'logs.db'}"
    from app.db import Base

    eng = create_engine(url)
    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng)() as db:
        old = AigcAudit(action="expired", payload={}, actor_id=1,
                        created_at=utcnow() - timedelta(days=settings.audit_retention_days + 1))
        fresh = AigcAudit(action="fresh", payload={}, actor_id=1, created_at=utcnow())
        db.add_all([old, fresh])
        db.commit()

    assert purge_logs_main(["--database-url", url]) == 0

    with sessionmaker(bind=eng)() as db:
        assert [r.action for r in db.query(AigcAudit).all()] == ["fresh"]
    eng.dispose()
