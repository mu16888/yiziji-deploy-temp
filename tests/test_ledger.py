"""Tests for the immutable credit ledger core (Task 4)."""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.domain.ledger import (
    InsufficientCredits,
    ReservationNotFound,
    append_entry,
    refund_unused,
    renew_expiry,
    reserve,
    settle,
)
from app.models import CreditEntry, Order, User
from app.services.credits import get_balance


def _make_engine(path):
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def engine(tmp_path):
    eng = _make_engine(tmp_path / "ledger.db")
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    yield session
    session.close()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _grant(db, uid, delta, key, *, ref_type="signup", ref_id="1", expires_at=None, pool="universal"):
    return append_entry(
        db,
        user_id=uid,
        delta=delta,
        entry_type="grant",
        ref_type=ref_type,
        ref_id=ref_id,
        idempotency_key=key,
        pool=pool,
        expires_at=expires_at,
    )


def _order(db, no, uid, paid_fen):
    db.add(
        Order(
            no=no,
            user_id=uid,
            sku="pkg_personal_699",
            amount_fen=paid_fen,
            paid_amount_fen=paid_fen,
            status="fulfilled",
        )
    )
    db.flush()


# --- Brief tests -------------------------------------------------------------


def test_append_entry_is_immutable_and_idempotent(db):
    """The brief's two assertions, verbatim, plus immutability of history."""
    e1 = append_entry(
        db, user_id=1, delta=100, entry_type="grant", ref_type="signup",
        ref_id="1", idempotency_key="k1",
    )
    e2 = append_entry(
        db, user_id=1, delta=100, entry_type="grant", ref_type="signup",
        ref_id="1", idempotency_key="k1",
    )
    assert e1.id == e2.id and get_balance(db, 1) == 100

    # A replay with a different payload under the same key cannot rewrite history.
    e3 = append_entry(
        db, user_id=1, delta=999, entry_type="adjust", ref_type="signup",
        ref_id="1", idempotency_key="k1",
    )
    assert e3.id == e1.id
    assert e3.delta == 100 and e3.entry_type == "grant"
    assert db.query(CreditEntry).filter_by(idempotency_key="k1").count() == 1


def test_concurrent_debit_never_negative(engine):
    """Balance 100; two concurrent reserve(60): one wins, one raises; no overdraft."""
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    seed = Session()
    _grant(seed, 1, 100, "signup-grant:1")
    seed.commit()
    seed.close()

    barrier = threading.Barrier(2)
    outcomes = {}

    def worker(name):
        s = Session()
        try:
            barrier.wait(timeout=10)
            reserve(
                s, user_id=1, amount=60, ref_type="job", ref_id=name,
                idempotency_key=f"reserve:{name}",
            )
            s.commit()
            outcomes[name] = "ok"
        except InsufficientCredits:
            s.rollback()
            outcomes[name] = "insufficient"
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("job-a", "job-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert sorted(outcomes.values()) == ["insufficient", "ok"]
    check = Session()
    assert get_balance(check, 1) == 40
    assert get_balance(check, 1) >= 0
    check.close()


def test_refund_after_spend_never_negative(db):
    """grant 100 -> reserve 80 -> refund_unused: balance stays 20, refund = unused fen."""
    _order(db, "ORD-1", 1, paid_fen=10000)
    append_entry(
        db, user_id=1, delta=100, entry_type="purchase", ref_type="order",
        ref_id="ORD-1", idempotency_key="purchase:ORD-1",
    )
    db.commit()

    reserve(
        db, user_id=1, amount=80, ref_type="job", ref_id="job-1",
        idempotency_key="reserve:job-1",
    )
    db.commit()
    assert get_balance(db, 1) == 20

    refunded = refund_unused(db, order_no="ORD-1", idempotency_key="refund:ORD-1")
    db.commit()
    assert refunded == 2000  # 10000 fen * 20 unused / 100 granted
    assert get_balance(db, 1) == 20  # never negative, account not frozen

    # Retry: same fen, one audit row.
    assert refund_unused(db, order_no="ORD-1", idempotency_key="refund:ORD-1") == 2000
    assert db.query(CreditEntry).filter_by(idempotency_key="refund:ORD-1").count() == 1


def test_refund_unused_per_lot_not_pool_balance(db):
    """A refund returns only its own lot's unused credits, not the whole pool's."""
    _order(db, "ORD-A", 1, paid_fen=10000)
    _order(db, "ORD-B", 1, paid_fen=20000)
    append_entry(db, user_id=1, delta=100, entry_type="purchase", ref_type="order",
                 ref_id="ORD-A", idempotency_key="purchase:ORD-A")
    append_entry(db, user_id=1, delta=100, entry_type="purchase", ref_type="order",
                 ref_id="ORD-B", idempotency_key="purchase:ORD-B")
    db.commit()

    reserve(db, user_id=1, amount=50, ref_type="job", ref_id="job-1",
            idempotency_key="reserve:job-1")
    db.commit()
    assert get_balance(db, 1) == 150

    # FIFO spends the older lot A first: A has 50 unused, B still has 100.
    assert refund_unused(db, order_no="ORD-A", idempotency_key="refund:ORD-A") == 5000
    db.commit()
    assert refund_unused(db, order_no="ORD-B", idempotency_key="refund:ORD-B") == 20000
    db.commit()
    assert get_balance(db, 1) == 150  # refund markers never freeze or drain credits


def test_renew_extends_active_lots_only(db):
    """One active lot + one expired lot: only the active lot's expiry changes."""
    now = _now()
    active = _grant(db, 1, 100, "lot-active", expires_at=now + timedelta(days=5))
    expired = _grant(db, 1, 50, "lot-expired", expires_at=now - timedelta(days=5))
    db.commit()

    new_expiry = now + timedelta(days=730)
    assert renew_expiry(db, user_id=1, new_expiry=new_expiry) == 1
    db.commit()

    db.refresh(active)
    db.refresh(expired)
    assert active.expires_at == new_expiry
    assert expired.expires_at != new_expiry


# --- Additional ledger behaviour --------------------------------------------


def test_retry_produces_one_row_for_deterministic_keys(db):
    """Deterministic keys from logical operations: retry never creates a second row."""
    _grant(db, 1, 100, "signup-grant:1")
    for _ in range(2):
        _grant(db, 1, 100, "signup-grant:1")
    assert db.query(CreditEntry).filter_by(idempotency_key="signup-grant:1").count() == 1

    for _ in range(2):
        reserve(
            db, user_id=1, amount=40, ref_type="job", ref_id="job-1",
            idempotency_key="reserve:job-1",
        )
    assert db.query(CreditEntry).filter_by(idempotency_key="reserve:job-1").count() == 1
    assert get_balance(db, 1) == 60

    for _ in range(2):
        settle(
            db, user_id=1, reserved_ref_id="job-1", actual_amount=30,
            idempotency_key="settle:job-1",
        )
    assert db.query(CreditEntry).filter_by(idempotency_key="settle:job-1").count() == 1
    assert get_balance(db, 1) == 70


def test_reserve_settlement_refunds_difference(db):
    _grant(db, 1, 100, "signup-grant:1")
    reserve(db, user_id=1, amount=60, ref_type="job", ref_id="job-1", idempotency_key="reserve:job-1")
    assert get_balance(db, 1) == 40
    entry = settle(db, user_id=1, reserved_ref_id="job-1", actual_amount=40, idempotency_key="settle:job-1")
    assert entry.delta == 20
    assert get_balance(db, 1) == 60


def test_settle_charges_extra_when_actual_exceeds_reserved(db):
    _grant(db, 1, 100, "signup-grant:1")
    reserve(db, user_id=1, amount=60, ref_type="job", ref_id="job-1", idempotency_key="reserve:job-1")
    entry = settle(db, user_id=1, reserved_ref_id="job-1", actual_amount=70, idempotency_key="settle:job-1")
    assert entry.delta == -10
    assert get_balance(db, 1) == 30


def test_settle_unknown_reservation_raises(db):
    with pytest.raises(ReservationNotFound):
        settle(db, user_id=1, reserved_ref_id="missing", actual_amount=10, idempotency_key="settle:missing")


def test_reserve_rejects_overdraw_and_keeps_balance(db):
    _grant(db, 1, 100, "signup-grant:1")
    reserve(db, user_id=1, amount=60, ref_type="job", ref_id="job-1", idempotency_key="r1")
    with pytest.raises(InsufficientCredits):
        reserve(db, user_id=1, amount=50, ref_type="job", ref_id="job-2", idempotency_key="r2")
    assert get_balance(db, 1) == 40
    assert db.query(CreditEntry).filter_by(idempotency_key="r2").count() == 0


def test_reserve_amount_must_be_positive(db):
    with pytest.raises(ValueError):
        reserve(db, user_id=1, amount=0, ref_type="job", ref_id="job-0", idempotency_key="r0")


def test_get_balance_pool_filter(db):
    _grant(db, 1, 100, "universal-1")
    _grant(db, 1, 50, "dedicated-1", pool="dedicated")
    assert get_balance(db, 1) == 150
    assert get_balance(db, 1, pool="universal") == 100
    assert get_balance(db, 1, pool="dedicated") == 50


def test_refund_full_manual_mode_returns_zero(db, monkeypatch):
    _order(db, "ORD-2", 1, paid_fen=5000)
    append_entry(db, user_id=1, delta=50, entry_type="purchase", ref_type="order",
                 ref_id="ORD-2", idempotency_key="purchase:ORD-2")
    monkeypatch.setattr(settings, "refund_mode", "full_manual")
    assert refund_unused(db, order_no="ORD-2", idempotency_key="refund:ORD-2") == 0
    assert get_balance(db, 1) == 50


def test_renew_new_only_mode_renews_latest_lot(db, monkeypatch):
    now = _now()
    first = _grant(db, 1, 100, "lot-1", expires_at=now + timedelta(days=5))
    second = _grant(db, 1, 100, "lot-2", expires_at=now + timedelta(days=6))
    db.commit()
    monkeypatch.setattr(settings, "renew_mode", "new_only")
    new_expiry = now + timedelta(days=730)
    assert renew_expiry(db, user_id=1, new_expiry=new_expiry) == 1
    db.refresh(first)
    db.refresh(second)
    assert second.expires_at == new_expiry
    assert first.expires_at != new_expiry
