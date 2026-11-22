"""Tests for AIGC compliance marking and audit retention (Task 11).

The environment defaults are verbatim from the brief: AIGC_LABEL=true and
AUDIT_RETENTION_DAYS=190. Marking is metadata-only (no media binary stamping).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base, get_db
from app.domain.generation import complete_job, create_job
from app.domain.ledger import append_entry
from app.main import app
from app.models import AigcAudit, AuditLog, GenerationJob, Product, User
from app.models.base import utcnow
from app.services import compliance
from app.services.auth import issue_token
from app.services.compliance import mark_aigc
from app.services.orders import confirm_payment, create_order
from scripts.purge_logs import main as purge_logs_main
from scripts.seed import PRODUCTS


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


@pytest.fixture()
def audit_db(tmp_path):
    """DB with seeded products/users for exercising real money/admin flows."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'audit-trail.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    for data in PRODUCTS:
        session.add(Product(**data))
    session.add_all(
        [
            User(phone="13900000003", display_name="admin", role="admin", is_active=True),
            User(phone="13800000001", display_name="user", role="user", is_active=True),
        ]
    )
    session.commit()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def admin_client(audit_db):
    def override_get_db():
        yield audit_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            admin = audit_db.query(User).filter_by(role="admin").one()
            client.headers.update({"Authorization": f"Bearer {issue_token(admin)}"})
            yield client
    finally:
        app.dependency_overrides.clear()


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


def test_money_admin_operations_write_audit_trail(audit_db, admin_client):
    """Money/admin writes are audited in the dedicated AuditLog table (T5/T6/T9).

    AigcAudit is reserved for AIGC label events; this test verifies the two
    audit stores are not conflated and that both money paths leave a trail.
    """
    admin = audit_db.query(User).filter_by(role="admin").one()
    user = audit_db.query(User).filter_by(role="user").one()

    # Order fulfillment (money operation) -> entitlement grant audit row.
    order = create_order(audit_db, user_id=user.id, sku="pkg_personal_699",
                         channel="native")
    confirm_payment(audit_db, order_no=order.no, event_id="audit-evt",
                    paid_amount_fen=69900)
    grant = audit_db.query(AuditLog).filter_by(action="bonus:grant").one()
    assert grant.target_id == str(user.id)
    assert grant.after["sku"] == "pkg_personal_699"

    # Admin credit adjustment (money + admin operation) -> audited with reason.
    resp = admin_client.post(
        "/admin/credits/adjust",
        json={"user_id": user.id, "delta": 50, "reason": "manual grant"},
    )
    assert resp.status_code == 200
    adjust = audit_db.query(AuditLog).filter_by(action="admin:credit_adjust").one()
    assert adjust.actor_id == admin.id
    assert adjust.reason == "manual grant"
    assert adjust.after["delta"] == 50

    # No money/admin event was duplicated into the AIGC-specific audit table.
    assert audit_db.query(AigcAudit).count() == 0


# --- retention / CLI ----------------------------------------------------------


def test_audit_retention(db):
    retention = settings.audit_retention_days
    assert retention == 190  # pin the brief's literal default, not just "whatever settings says"
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
