"""Order lifecycle service: create, start payment, confirm (idempotent), cancel/refund.

All money is integer 分. Every credit grant, bonus application, commission hook
and event write happens inside the single transaction owned by ``confirm_payment``;
the webhook event id plus the DB unique key ``(order_no, event_id)`` make a
replayed callback a no-op.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.payment import PAYMENT_CHANNELS, get_gateway
from app.config import settings
from app.domain.ledger import append_entry, refund_unused, renew_expiry
from app.domain.order import (
    CLOSED,
    CREATED,
    FULFILLED,
    PAID,
    PENDING_PAYMENT,
    REFUNDED,
    REFUNDING,
    assert_transition,
)
from app.models import CreditEntry, Order, OrderEvent, Product, User
from app.services.affiliate import apply_order_commission, reverse_order_commission
from app.services.catalog import apply_bonuses, get_price, revoke_bonuses

# Marker set on an order returned from pending-order recovery so the API can
# tell the client "continue paying this one" instead of creating a duplicate.
PENDING_ORDER_PAYMENT_IN_PROGRESS = "pending_order_payment_in_progress"


class OrderError(Exception):
    """Base class for order service errors."""


class OrderNotFound(OrderError, LookupError):
    """Raised when an order number does not exist."""


class InvalidChannel(OrderError, ValueError):
    """Raised for a payment channel the template does not support."""


class InvalidOrderState(OrderError):
    """Raised when an operation is illegal in the order's current status."""


class NotPurchasable(OrderError):
    """Raised when the SKU's catalogue row is not purchasable."""


class PaymentAmountMismatch(OrderError):
    """Raised when a paid amount differs from the server-authoritative price.

    The order is left in ``pending_payment`` so a human can review it; no
    credits, bonuses or commission are granted.
    """


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _new_order_no(db: Session) -> str:
    while True:
        candidate = f"ORD-{uuid.uuid4().hex[:16].upper()}"
        if db.query(Order.id).filter_by(no=candidate).first() is None:
            return candidate


def _get_order(db: Session, order_no: str) -> Order:
    order = db.query(Order).filter_by(no=str(order_no)).first()
    if order is None:
        raise OrderNotFound(f"unknown order: {order_no}")
    return order


def _record_event(
    db: Session,
    order: Order,
    event_type: str,
    *,
    event_id: str | None = None,
    payload: dict | None = None,
) -> OrderEvent:
    """Get-or-create an order event; deterministically keyed, never duplicated."""
    if event_id is None:
        existing = (
            db.query(OrderEvent)
            .filter_by(order_no=order.no, event_type=event_type)
            .first()
        )
    else:
        existing = (
            db.query(OrderEvent)
            .filter_by(order_no=order.no, event_id=event_id)
            .first()
        )
    if existing is not None:
        return existing

    key = f"event:{order.no}:{event_type}:{event_id or 'na'}"
    event = OrderEvent(
        order_id=order.id,
        order_no=order.no,
        event_id=event_id,
        event_type=event_type,
        payload=payload or {},
        idempotency_key=key,
    )
    db.add(event)
    db.flush()
    return event


def _credits_for_sku(db: Session, sku: str) -> int:
    product = db.query(Product).filter_by(sku=sku).first()
    return int(product.credits) if product is not None else 0


def create_order(db: Session, *, user_id: int, sku: str, channel: str) -> Order:
    """Create a pending order at the server-computed price, or resume a pending one.

    A second call for the same user + SKU while the first order is still
    ``pending_payment`` returns that same order with ``recovery`` set to
    ``pending_order_payment_in_progress`` — no duplicate row, no duplicate pay.
    """
    if channel not in PAYMENT_CHANNELS:
        raise InvalidChannel(f"unsupported payment channel: {channel}")

    user = db.get(User, int(user_id))
    if user is None:
        raise OrderNotFound(f"unknown user: {user_id}")

    quote = get_price(db, sku, user)
    if not quote.eligible:
        raise NotPurchasable(f"sku is not purchasable: {sku}")

    pending = (
        db.query(Order)
        .filter_by(user_id=int(user_id), sku=sku, status=PENDING_PAYMENT)
        .order_by(Order.id.desc())
        .first()
    )
    if pending is not None:
        pending.recovery = PENDING_ORDER_PAYMENT_IN_PROGRESS
        return pending

    product = db.query(Product).filter_by(sku=sku).first()
    no = _new_order_no(db)
    order = Order(
        no=no,
        user_id=int(user_id),
        product_id=product.id if product is not None else None,
        sku=sku,
        amount_fen=int(quote.amount_fen),
        status=CREATED,
        channel=channel,
        idempotency_key=f"order:{no}",
    )
    db.add(order)
    db.flush()

    assert_transition(order.status, PENDING_PAYMENT)
    order.status = PENDING_PAYMENT
    _record_event(
        db,
        order,
        "order_created",
        payload={"sku": sku, "channel": channel, "amount_fen": int(quote.amount_fen)},
    )
    db.commit()
    order.recovery = None
    return order


def start_payment(db: Session, order_no: str) -> dict:
    """Return the configured gateway's payload for a pending order."""
    order = _get_order(db, order_no)
    if order.status == CREATED:
        assert_transition(order.status, PENDING_PAYMENT)
        order.status = PENDING_PAYMENT
        db.commit()
    elif order.status != PENDING_PAYMENT:
        raise InvalidOrderState(f"cannot start payment in status {order.status!r}")
    return get_gateway().create_payment(order)


def confirm_payment(
    db: Session, *, order_no: str, event_id: str, paid_amount_fen: int
) -> Order:
    """Fulfil a paid order once, idempotently keyed by ``order_no + event_id``.

    On first delivery, one transaction grants the purchase credits, applies
    bonuses, calls the commission hook and writes the webhook event. Replays
    (same event id, or a different event id for an already-purchased order)
    return the order without granting credits again.
    """
    order_no = str(order_no)
    event_id = str(event_id)
    if isinstance(paid_amount_fen, bool) or not isinstance(paid_amount_fen, int):
        raise ValueError("paid_amount_fen must be an integer in fen")
    if paid_amount_fen < 0:
        raise ValueError("paid_amount_fen must be non-negative")

    replay = (
        db.query(OrderEvent).filter_by(order_no=order_no, event_id=event_id).first()
    )
    if replay is not None:
        return _get_order(db, order_no)

    order = _get_order(db, order_no)
    # The price is server-authoritative: a signed callback for another amount
    # is never fulfilled. Leave the order pending for manual review.
    if paid_amount_fen != int(order.amount_fen):
        raise PaymentAmountMismatch(
            f"paid amount {paid_amount_fen} != order price {order.amount_fen}"
        )

    already_purchased = (
        db.query(CreditEntry)
        .filter_by(idempotency_key=f"purchase:{order_no}")
        .first()
        is not None
    )
    if already_purchased or order.status == FULFILLED:
        _record_event(
            db,
            order,
            "payment_confirmed",
            event_id=event_id,
            payload={"paid_amount_fen": paid_amount_fen, "replay": True},
        )
        db.commit()
        return _get_order(db, order_no)

    if order.status == PAID:
        assert_transition(PAID, FULFILLED)
    elif order.status == PENDING_PAYMENT:
        assert_transition(PENDING_PAYMENT, PAID)
        assert_transition(PAID, FULFILLED)
    elif order.status == CREATED:
        assert_transition(CREATED, PENDING_PAYMENT)
        assert_transition(PENDING_PAYMENT, PAID)
        assert_transition(PAID, FULFILLED)
    else:
        raise InvalidOrderState(f"cannot confirm order in status {order.status!r}")

    now = _utcnow()
    try:
        with db.begin_nested():
            order.status = FULFILLED
            order.paid_amount_fen = paid_amount_fen
            order.paid_at = now
            order.fulfilled_at = now

            # Purchase credits expire CREDIT_EXPIRE_DAYS after the recharge;
            # the same recharge renews other active lots per RENEW_MODE, all
            # inside this one transaction.
            new_expiry = now + timedelta(days=settings.credit_expire_days)
            append_entry(
                db,
                user_id=order.user_id,
                delta=_credits_for_sku(db, order.sku),
                entry_type="purchase",
                ref_type="order",
                ref_id=order_no,
                idempotency_key=f"purchase:{order_no}",
                expires_at=new_expiry,
            )
            apply_bonuses(db, order.user_id, order.sku)
            apply_order_commission(db, order=order)
            renew_expiry(db, user_id=order.user_id, new_expiry=new_expiry)
            _record_event(
                db,
                order,
                "payment_confirmed",
                event_id=event_id,
                payload={"paid_amount_fen": paid_amount_fen},
            )
        db.commit()
    except IntegrityError:
        # A concurrent callback with the same event id won the unique-key race;
        # the savepoint rolled back our whole fulfilment attempt.
        db.rollback()
        if (
            db.query(OrderEvent)
            .filter_by(order_no=order_no, event_id=event_id)
            .first()
            is not None
        ):
            return _get_order(db, order_no)
        raise
    return _get_order(db, order_no)


def cancel_order(db: Session, order_no: str) -> Order:
    """Close an unpaid order; cancelling an already-closed order is a no-op."""
    order = _get_order(db, order_no)
    if order.status == CLOSED:
        return order
    if order.status not in (CREATED, PENDING_PAYMENT):
        raise InvalidOrderState(f"cannot cancel order in status {order.status!r}")
    previous = order.status
    assert_transition(previous, CLOSED)
    order.status = CLOSED
    order.closed_at = _utcnow()
    _record_event(db, order, "order_cancelled", payload={"from": previous})
    db.commit()
    return _get_order(db, order_no)


def begin_refund(db: Session, order_no: str) -> Order:
    """Move a paid/fulfilled order into ``refunding``."""
    order = _get_order(db, order_no)
    if order.status == REFUNDING:
        return order
    assert_transition(order.status, REFUNDING)
    previous = order.status
    order.status = REFUNDING
    _record_event(db, order, "refund_requested", payload={"from": previous})
    db.commit()
    return _get_order(db, order_no)


def finish_refund(
    db: Session, order_no: str, *, amount_fen: int | None = None
) -> Order:
    """Complete a refund in one transaction: reverse, revoke, log and settle.

    Reverses commission (T8 hook), writes the compensating entitlement marker,
    records the T4 refund ledger entry with deterministic key
    ``refund:{order_no}``, and writes ``refund_completed``. A zero unused-credit
    result is not an error -- the refund event is still recorded.
    """
    order = _get_order(db, order_no)
    if order.status == REFUNDED:
        return order
    assert_transition(order.status, REFUNDED)
    if amount_fen is not None:
        # Record and commit an idempotent "initiated" marker BEFORE the external
        # side effect: a crash after the gateway accepts the money leaves a
        # durable row the reconciler can find (refund_completed comes later).
        _record_event(
            db, order, "refund_initiated", payload={"amount_fen": amount_fen}
        )
        db.commit()
        if not get_gateway().refund(order_no, amount_fen):
            raise InvalidOrderState("payment gateway rejected the refund")

    try:
        with db.begin_nested():
            order.status = REFUNDED
            order.refund_status = "refunded"
            reverse_order_commission(db, order=order)
            revoke_bonuses(
                db, user_id=order.user_id, sku=order.sku, order_no=order_no
            )
            # T4 derives the refund user from this order's purchase lots; a
            # paid-but-never-fulfilled order has none, so there is no credit
            # side to refund in that case.
            has_purchase = (
                db.query(CreditEntry.id)
                .filter_by(ref_type="order", ref_id=order_no, entry_type="purchase")
                .first()
                is not None
            )
            if has_purchase:
                refund_unused(
                    db, order_no=order_no, idempotency_key=f"refund:{order_no}"
                )
            _record_event(
                db, order, "refund_completed", payload={"amount_fen": amount_fen}
            )
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.query(Order).filter_by(no=order_no).first().status == REFUNDED:
            return _get_order(db, order_no)
        raise
    return _get_order(db, order_no)
