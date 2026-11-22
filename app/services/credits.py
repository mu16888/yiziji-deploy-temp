"""Credit balance reads plus the ledger operations re-exported for callers.

The router in ``app/api/credits.py`` keeps calling ``get_balance``. Later tasks
import ``reserve``/``settle``/``refund_unused``/``renew_expiry`` from either this
module or ``app.domain.ledger`` -- both resolve to the same implementations.
"""

from sqlalchemy import func

from app.domain.ledger import (
    InsufficientCredits,
    LedgerError,
    ReservationNotFound,
    append_entry,
    refund_unused,
    renew_expiry,
    reserve,
    settle,
)
from app.models import CreditEntry

__all__ = [
    "get_balance",
    "append_entry",
    "reserve",
    "settle",
    "refund_unused",
    "renew_expiry",
    "LedgerError",
    "InsufficientCredits",
    "ReservationNotFound",
]


def get_balance(db, user_id: int, pool: str | None = None) -> int:
    """Current balance from the immutable ledger (optionally for one pool)."""
    query = db.query(func.coalesce(func.sum(CreditEntry.delta), 0)).filter(
        CreditEntry.user_id == user_id
    )
    if pool is not None:
        query = query.filter(CreditEntry.pool == pool)
    return int(query.scalar() or 0)
