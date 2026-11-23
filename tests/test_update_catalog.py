"""Tests for the idempotent live-DB catalogue migration (scripts/update_catalog)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Product


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _legacy_catalog(db):
    """Simulate the pre-adjustment production DB."""
    db.add(
        Product(
            sku="svc_voice_clone_299",
            name="专属音色复刻",
            type="service",
            amount_fen=29900,
            original_amount_fen=None,
            credits=0,
            bonuses={},
            eligibility={},
            sort=5,
            active=True,
        )
    )
    db.add(
        Product(
            sku="agent_admission_9999",
            name="正式代理准入",
            type="agent",
            amount_fen=999900,
            credits=0,
            bonuses={"agent": True},
            eligibility={},
            sort=6,
            active=True,
        )
    )
    db.commit()


def test_update_catalog_adds_starter_and_deactivates_voice(db):
    from scripts.update_catalog import update_catalog

    _legacy_catalog(db)
    actions = update_catalog(db)

    assert any("pkg_starter_9" in a and "added" in a for a in actions)
    assert any("svc_voice_clone_299" in a and "deactivat" in a for a in actions)

    starter = db.query(Product).filter_by(sku="pkg_starter_9").one()
    assert starter.amount_fen == 990 and starter.credits == 100 and starter.sort == 0
    voice = db.query(Product).filter_by(sku="svc_voice_clone_299").one()
    assert voice.active is False  # soft-delete, never hard-delete
    agent = db.query(Product).filter_by(sku="agent_admission_9999").one()
    assert agent.active is True  # real feature, hidden by code, not deactivated


def test_update_catalog_is_idempotent(db):
    from scripts.update_catalog import update_catalog

    _legacy_catalog(db)
    first = update_catalog(db)
    counts = (
        db.query(Product).count(),
        db.query(Product).filter_by(sku="svc_voice_clone_299", active=True).count(),
    )
    second = update_catalog(db)
    assert (
        db.query(Product).count(),
        db.query(Product).filter_by(sku="svc_voice_clone_299", active=True).count(),
    ) == counts
    assert not any("added" in a for a in second)
    assert not any("deactivat" in a for a in second)
    # Guard against a no-op implementation: the first run did real work.
    assert first and first != second
