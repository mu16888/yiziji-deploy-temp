"""Temporary MINIMAL ledger stub for Task 3 only.

Task 4 replaces ``append_entry`` in place with the real immutable ledger,
keeping this exact signature. Do not add ledger business logic here.
"""

from datetime import datetime

from sqlalchemy import func

from app.models import CreditBalance, CreditEntry


def append_entry(
    db,
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
    """Insert one credit entry and keep the cached balance row in sync."""
    existing = db.query(CreditEntry).filter_by(idempotency_key=idempotency_key).first()
    if existing is not None:
        return existing

    prior = (
        db.query(func.coalesce(func.sum(CreditEntry.delta), 0))
        .filter(CreditEntry.user_id == user_id, CreditEntry.pool == pool)
        .scalar()
        or 0
    )
    new_balance = int(prior) + int(delta)

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

    balance = db.query(CreditBalance).filter_by(user_id=user_id, pool=pool).first()
    if balance is None:
        db.add(CreditBalance(user_id=user_id, pool=pool, balance=new_balance))
    else:
        balance.balance = new_balance
    db.flush()
    return entry
