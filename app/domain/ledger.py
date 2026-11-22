"""Immutable, idempotent credit ledger core.

Pure domain module: no FastAPI / Request imports. Every mutation runs inside the
caller's SQLAlchemy transaction (we flush, we never commit) and uses an atomic
conditional ``UPDATE`` on the cached balance row. That single statement both
checks ``balance >= 0`` and applies the delta, so two concurrent debits cannot
overdraw: SQLite serialises writers at that statement, and on Postgres the
same update is atomic.

All money and credits are integers (fen); no floats are stored or computed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CreditBalance, CreditEntry, Order

# Positive entry types that represent a "lot" of granted credits.
_GRANT_TYPES = ("grant", "purchase", "bonus", "adjust")
_POOL_UNIVERSAL = "universal"


class LedgerError(Exception):
    """Base class for ledger domain errors."""


class InsufficientCredits(LedgerError):
    """Raised when a debit would drive the balance below zero."""


class ReservationNotFound(LedgerError):
    """Raised when settle cannot find the reservation it must reconcile."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _write_entry(
    db: Session,
    *,
    user_id: int,
    delta: int,
    entry_type: str,
    ref_type: str | None,
    ref_id: str,
    idempotency_key: str,
    pool: str,
    expires_at: datetime | None,
) -> CreditEntry:
    """Atomically move the cached balance and append one ledger row.

    The caller must already have checked the idempotency key (see
    ``append_entry``). A failed balance move raises before any row is written.
    """
    balance = db.query(CreditBalance).filter_by(user_id=user_id, pool=pool).first()
    if balance is None:
        balance = CreditBalance(user_id=user_id, pool=pool, balance=0)
        db.add(balance)
        db.flush()

    # Atomic guard: the row is only updated when the resulting balance stays
    # non-negative. Concurrent writers are serialised by this UPDATE (SQLite)
    # or by the row lock it takes (Postgres); rowcount == 0 means not enough
    # credits, with no partial mutation to roll back.
    moved = db.execute(
        update(CreditBalance)
        .where(CreditBalance.id == balance.id)
        .where(CreditBalance.balance >= -delta)
        .values(balance=CreditBalance.balance + delta)
        .execution_options(synchronize_session=False)
    )
    if moved.rowcount != 1:
        raise InsufficientCredits(
            f"insufficient credits in pool {pool!r} for delta {delta}"
        )

    new_balance = int(
        db.query(CreditBalance.balance).filter_by(id=balance.id).scalar() or 0
    )
    entry = CreditEntry(
        user_id=user_id,
        delta=delta,
        balance_after=new_balance,
        entry_type=entry_type,
        ref_type=ref_type,
        ref_id=str(ref_id),
        idempotency_key=idempotency_key,
        pool=pool,
        expires_at=expires_at,
    )
    db.add(entry)
    db.flush()
    return entry


def append_entry(
    db: Session,
    *,
    user_id: int,
    delta: int,
    entry_type: str,
    ref_type: str,
    ref_id: str,
    idempotency_key: str,
    pool: str = "universal",
    expires_at: datetime | None = None,
) -> CreditEntry:
    """Append one immutable ledger row, idempotently.

    A replay of the same ``idempotency_key`` returns the original row unchanged
    (even if the payload differs), so retries never double-credit or rewrite
    history. Negative deltas that would break ``balance >= 0`` raise
    ``InsufficientCredits``. Flushes only; the caller owns the transaction.
    """
    if isinstance(delta, float):
        raise ValueError("credits must be integers (no floats)")
    delta = int(delta)

    existing = (
        db.query(CreditEntry).filter_by(idempotency_key=idempotency_key).first()
    )
    if existing is not None:
        return existing

    try:
        with db.begin_nested():  # savepoint: a race must not undo caller work
            entry = _write_entry(
                db,
                user_id=user_id,
                delta=delta,
                entry_type=entry_type,
                ref_type=ref_type,
                ref_id=str(ref_id),
                idempotency_key=idempotency_key,
                pool=pool,
                expires_at=expires_at,
            )
    except IntegrityError:
        # Another transaction inserted the same key first; return its row.
        existing = (
            db.query(CreditEntry).filter_by(idempotency_key=idempotency_key).first()
        )
        if existing is not None:
            return existing
        raise
    return entry


def reserve(
    db: Session,
    *,
    user_id: int,
    amount: int,
    ref_type: str,
    ref_id: str,
    idempotency_key: str,
    pool: str = "universal",
) -> CreditEntry:
    """Pre-debit ``amount`` credits for a job; raise if the balance is too low.

    ``idempotency_key`` must be derived from the logical operation (e.g.
    ``reserve:{job_ref}``) so a retry produces exactly one ``consume`` row.
    """
    if isinstance(amount, float) or int(amount) != amount:
        raise ValueError("credits must be integers (no floats)")
    amount = int(amount)
    if amount <= 0:
        raise ValueError("reserve amount must be positive")
    return append_entry(
        db,
        user_id=user_id,
        delta=-amount,
        entry_type="consume",
        ref_type=ref_type,
        ref_id=ref_id,
        idempotency_key=idempotency_key,
        pool=pool,
    )


def settle(
    db: Session,
    *,
    user_id: int,
    reserved_ref_id: str,
    actual_amount: int,
    idempotency_key: str,
) -> CreditEntry:
    """Reconcile a reservation against the real usage (多退少补).

    ``reserved - actual`` is appended as a ``settle`` row: positive refunds the
    unused reservation, negative charges the extra usage (still bounded by
    ``balance >= 0``). The original reservation is located by ``reserved_ref_id``.
    """
    existing = (
        db.query(CreditEntry).filter_by(idempotency_key=idempotency_key).first()
    )
    if existing is not None:
        return existing

    reservation = (
        db.query(CreditEntry)
        .filter(
            CreditEntry.user_id == user_id,
            CreditEntry.ref_id == str(reserved_ref_id),
            CreditEntry.entry_type == "consume",
        )
        .order_by(CreditEntry.id.desc())
        .first()
    )
    if reservation is None:
        raise ReservationNotFound(f"no reservation for {reserved_ref_id!r}")

    if isinstance(actual_amount, float) or int(actual_amount) != actual_amount:
        raise ValueError("credits must be integers (no floats)")
    actual = int(actual_amount)
    if actual < 0:
        raise ValueError("actual_amount must be non-negative")

    diff = -int(reservation.delta) - actual
    return append_entry(
        db,
        user_id=user_id,
        delta=diff,
        entry_type="settle",
        ref_type="settle",
        ref_id=str(reserved_ref_id),
        idempotency_key=idempotency_key,
        pool=reservation.pool,
    )


def _fen_from_reason(reason: str | None) -> int:
    for part in (reason or "").split(";"):
        if part.startswith("refund_fen:"):
            try:
                return int(part.split(":", 1)[1])
            except ValueError:
                return 0
    return 0


def refund_unused(
    db: Session, *, order_no: str, idempotency_key: str
) -> int:
    """Return the fen to refund for an order's *unused* credits.

    Default ``REFUND_MODE=unused_only``: only the unused portion of the order's
    lot is returned; the spent portion goes to manual review. The ledger is
    never driven negative and the account is not frozen -- a zero-delta
    ``refund`` row records the decision idempotently, so a retry returns the
    same fen and creates no second row. ``full_manual`` returns 0.
    """
    existing = (
        db.query(CreditEntry).filter_by(idempotency_key=idempotency_key).first()
    )
    if existing is not None:
        return _fen_from_reason(existing.reason)

    lots = (
        db.query(CreditEntry)
        .filter(
            CreditEntry.ref_id == str(order_no),
            CreditEntry.delta > 0,
            CreditEntry.entry_type.in_(_GRANT_TYPES),
        )
        .order_by(CreditEntry.id)
        .all()
    )
    order = db.query(Order).filter_by(no=str(order_no)).first() if lots else None
    user_id = lots[0].user_id if lots else (order.user_id if order else 0)
    pool = lots[0].pool if lots else _POOL_UNIVERSAL
    granted = sum(int(e.delta) for e in lots)

    # Credits are consumed oldest-lot-first, so bound the refund by *this
    # order's own lots* still-unused credits -- not by the whole pool balance,
    # which would hand one order another order's unused credits (over-refund).
    unused = 0
    if lots:
        pool_lots = (
            db.query(CreditEntry)
            .filter(
                CreditEntry.user_id == user_id,
                CreditEntry.pool == pool,
                CreditEntry.delta > 0,
                CreditEntry.entry_type.in_(_GRANT_TYPES),
            )
            .order_by(CreditEntry.id)
            .all()
        )
        remaining = _lot_remaining(db, user_id, pool, pool_lots)
        unused = min(granted, sum(remaining.get(lot.id, 0) for lot in lots))

    fen = 0
    if settings.refund_mode == "unused_only" and granted > 0 and unused > 0:
        paid = int(order.paid_amount_fen) if order and order.paid_amount_fen is not None else None
        fen = paid * unused // granted if paid is not None else unused
        if paid is not None:
            fen = min(fen, paid)

    marker = append_entry(
        db,
        user_id=user_id,
        delta=0,
        entry_type="refund",
        ref_type="order",
        ref_id=str(order_no),
        idempotency_key=idempotency_key,
        pool=pool,
    )
    marker.reason = f"refund_fen:{fen}"
    db.flush()
    return fen


def _lot_remaining(
    db: Session, user_id: int, pool: str, lots: list[CreditEntry]
) -> dict[int, int]:
    """Remaining credits per lot, consuming debits oldest-lot-first (FIFO).

    Positive ``settle``/``refund`` deltas return credits that a reservation had
    previously consumed (多退少补). FIFO debits always draw from the oldest lot
    first, so a returned credit is restored to that oldest lot; ignoring these
    returns understates an order's truly-unused credits after a partial settle.
    """
    remaining = {lot.id: int(lot.delta) for lot in lots}
    lot_ids = [lot.id for lot in lots]
    movements: list[CreditEntry] = []
    if lot_ids:
        movements = (
            db.query(CreditEntry)
            .filter(
                CreditEntry.user_id == user_id,
                CreditEntry.pool == pool,
                CreditEntry.id.notin_(lot_ids),
            )
            .order_by(CreditEntry.id)
            .all()
        )
    for entry in movements:
        delta = int(entry.delta)
        if delta > 0:
            # A settle/refund return: restore to the FIFO-origin (oldest) lot.
            remaining[lot_ids[0]] += delta
            continue
        owed = -delta
        for lot in lots:
            if owed <= 0:
                break
            taken = min(remaining[lot.id], owed)
            remaining[lot.id] -= taken
            owed -= taken
    return remaining


def renew_expiry(db: Session, *, user_id: int, new_expiry: datetime) -> int:
    """Extend active universal lots to ``new_expiry``; return lots renewed.

    ``RENEW_MODE=all_active`` (default) renews every unexpired, unspent universal
    lot; ``new_only`` renews just the newest active lot (the one the recharge
    created). Expired or fully-consumed lots are never touched.
    """
    lots = (
        db.query(CreditEntry)
        .filter(
            CreditEntry.user_id == user_id,
            CreditEntry.pool == _POOL_UNIVERSAL,
            CreditEntry.delta > 0,
            CreditEntry.entry_type.in_(_GRANT_TYPES),
        )
        .order_by(CreditEntry.id)
        .all()
    )
    if not lots:
        return 0

    remaining = _lot_remaining(db, user_id, _POOL_UNIVERSAL, lots)
    now = _utcnow()
    active = [
        lot
        for lot in lots
        if remaining.get(lot.id, 0) > 0
        and (lot.expires_at is None or lot.expires_at > now)
    ]
    if settings.renew_mode == "new_only":
        active = active[-1:]

    for lot in active:
        lot.expires_at = new_expiry
    db.flush()
    return len(active)
