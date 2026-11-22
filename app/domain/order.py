"""Order state machine: statuses, legal transitions and guards.

Pure domain module — no FastAPI and no SQLAlchemy session work lives here.
The webhook idempotency key itself is enforced by the DB unique constraint on
``order_events(order_no, event_id)``; this module only decides which status
changes are legal.
"""

from __future__ import annotations

# Canonical status values (stored verbatim in ``orders.status``).
CREATED = "created"
PENDING_PAYMENT = "pending_payment"
PAID = "paid"
FULFILLED = "fulfilled"
CLOSED = "closed"
REFUNDING = "refunding"
REFUNDED = "refunded"

ALL_STATUSES = frozenset(
    {CREATED, PENDING_PAYMENT, PAID, FULFILLED, CLOSED, REFUNDING, REFUNDED}
)

# created -> pending_payment -> paid -> fulfilled -> closed / refunding -> refunded
# ``pending_payment -> fulfilled`` is the compound "confirm in one transaction"
# transition used by confirm_payment; the stepwise route via ``paid`` is kept
# as two separate legal edges as well.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    CREATED: frozenset({PENDING_PAYMENT, CLOSED}),
    PENDING_PAYMENT: frozenset({PAID, FULFILLED, CLOSED}),
    PAID: frozenset({FULFILLED, REFUNDING, CLOSED}),
    FULFILLED: frozenset({REFUNDING, CLOSED}),
    REFUNDING: frozenset({REFUNDED, FULFILLED}),
    REFUNDED: frozenset(),
    CLOSED: frozenset(),
}


class OrderStateError(Exception):
    """Base class for order-state domain errors."""


class InvalidOrderTransition(OrderStateError):
    """Raised when a requested status change is not in the state machine."""


def can_transition(current: str, target: str) -> bool:
    """True when ``current -> target`` is a legal edge."""
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(current: str, target: str) -> None:
    """Raise :class:`InvalidOrderTransition` for an illegal edge."""
    if not can_transition(current, target):
        raise InvalidOrderTransition(f"illegal order transition: {current} -> {target}")
