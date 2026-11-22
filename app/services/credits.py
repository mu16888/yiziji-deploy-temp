"""Temporary minimal balance reader for Task 3.

Task 4 replaces this implementation in place; the credits router stays the
same and keeps calling ``get_balance``.
"""

from sqlalchemy import func

from app.models import CreditEntry


def get_balance(db, user_id: int, pool: str | None = None) -> int:
    """Current balance from the credit ledger (optionally for one pool)."""
    query = db.query(func.coalesce(func.sum(CreditEntry.delta), 0)).filter(
        CreditEntry.user_id == user_id
    )
    if pool is not None:
        query = query.filter(CreditEntry.pool == pool)
    return int(query.scalar() or 0)
