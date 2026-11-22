"""Tests for ORM models and seed data (Task 2)."""

import pytest
from sqlalchemy import Integer, create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AigcAudit,
    Agent,
    AgentFundEntry,
    ALL_MODELS,
    Attribution,
    AuditLog,
    CommissionEntry,
    CreditBalance,
    CreditEntry,
    FUND_MODELS,
    GenerationJob,
    Invitation,
    Order,
    OrderEvent,
    Product,
    Promoter,
    Role,
    User,
    Withdrawal,
)
from scripts.seed import DEFAULT_SKUS, seed


def test_required_tables_and_columns():
    assert User.__tablename__ == "users"
    assert "identity_hash" in User.__table__.columns
    assert "org_id" in User.__table__.columns
    assert "idempotency_key" in CreditEntry.__table__.columns
    assert CreditEntry.__table__.columns["idempotency_key"].unique
    assert "amount_fen" in Product.__table__.columns


def test_all_expected_tables_exist():
    expected = {model.__tablename__ for model in ALL_MODELS}
    assert len(ALL_MODELS) == 17
    assert expected <= set(Base.metadata.tables)
    assert AigcAudit.__tablename__ == "aigc_audit"
    assert Role.__tablename__ == "user_roles"


@pytest.mark.parametrize(
    "model, columns",
    [
        (Product, ["amount_fen", "original_amount_fen"]),
        (Order, ["amount_fen", "paid_amount_fen"]),
        (AgentFundEntry, ["delta_fen", "balance_after_fen"]),
        (CommissionEntry, ["amount_fen"]),
        (Withdrawal, ["amount_fen"]),
    ],
)
def test_money_columns_are_integer_fen(model, columns):
    for column in columns:
        assert isinstance(model.__table__.columns[column].type, Integer), (
            f"{model.__name__}.{column} must be Integer (fen)"
        )


@pytest.mark.parametrize("model", FUND_MODELS)
def test_fund_tables_have_unique_non_null_idempotency_key(model):
    column = model.__table__.columns["idempotency_key"]
    assert column.unique
    assert not column.nullable
    assert "created_at" in model.__table__.columns


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


def test_seed_creates_default_catalog_roles_and_demo_accounts(db):
    seed(db)

    assert {p.sku for p in db.query(Product).all()} == DEFAULT_SKUS
    assert {r.name for r in db.query(Role).all()} == {
        "user",
        "promoter",
        "agent",
        "admin",
    }
    phones = {u.phone for u in db.query(User).all()}
    assert {"13900000001", "13900000002", "13900000003"} <= phones

    # Cross-task contract: T6's confirm_payment test expects 699 -> 6000 credits.
    assert db.query(Product).filter_by(sku="pkg_personal_699").one().credits == 6000
    assert db.query(Product).filter_by(sku="pkg_enterprise_collab_3999").one().credits == 45000


def test_seed_is_idempotent(db):
    seed(db)
    first_counts = (
        db.query(Product).count(),
        db.query(Role).count(),
        db.query(User).count(),
    )
    seed(db)
    assert (
        db.query(Product).count(),
        db.query(Role).count(),
        db.query(User).count(),
    ) == first_counts
