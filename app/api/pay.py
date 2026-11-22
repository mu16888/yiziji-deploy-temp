"""Payment endpoints: order creation, JSAPI/Native pay, webhook sync and cancel."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters.payment import InvalidSignature, get_gateway
from app.db import get_db
from app.models import Order
from app.services.auth import get_current_user
from app.services.catalog import ProductNotFound
from app.services.orders import (
    InvalidChannel,
    InvalidOrderState,
    NotPurchasable,
    OrderNotFound,
    PaymentAmountMismatch,
    cancel_order,
    confirm_payment,
    create_order,
    start_payment,
)

router = APIRouter(prefix="/api/pay", tags=["pay"])


class CreateOrderIn(BaseModel):
    sku: str
    channel: str


class OrderNoIn(BaseModel):
    order_no: str


def _load_owned(db: Session, order_no: str, user_id: int) -> Order:
    order = db.query(Order).filter_by(no=str(order_no)).first()
    if order is None or order.user_id != user_id:
        raise HTTPException(status_code=404, detail="order not found")
    return order


@router.post("/orders")
def orders(
    body: CreateOrderIn,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        order = create_order(
            db, user_id=current_user.id, sku=body.sku, channel=body.channel
        )
    except ProductNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (InvalidChannel, NotPurchasable) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        pay_params = start_payment(db, order.no)
    except InvalidOrderState as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "order_no": order.no,
        "status": order.status,
        "amount_fen": order.amount_fen,
        "sku": order.sku,
        "channel": order.channel,
        "recovery": getattr(order, "recovery", None),
        "pay_params": pay_params,
    }


@router.get("/orders/{order_no}")
def order_status(
    order_no: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    order = _load_owned(db, order_no, current_user.id)
    return {
        "order_no": order.no,
        "status": order.status,
        "refund_status": order.refund_status,
        "amount_fen": order.amount_fen,
        "paid_amount_fen": order.paid_amount_fen,
        "gateway_status": get_gateway().query(order.no),
    }


def _pay(body: OrderNoIn, channel: str, user_id: int, db: Session):
    order = _load_owned(db, body.order_no, user_id)
    if order.channel != channel:
        raise HTTPException(status_code=400, detail=f"order channel is not {channel}")
    try:
        return start_payment(db, order.no)
    except InvalidOrderState as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/native_pay")
def native_pay(
    body: OrderNoIn,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _pay(body, "native", current_user.id, db)


@router.post("/jsapi_pay")
def jsapi_pay(
    body: OrderNoIn,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _pay(body, "jsapi", current_user.id, db)


@router.post("/sync")
def sync(request: Request, body: dict, db: Session = Depends(get_db)):
    """Webhook: verify the gateway signature, then confirm idempotently."""
    signature = body.get("signature") or request.headers.get("x-pay-signature", "")
    try:
        order_no = get_gateway().verify_callback(body, signature)
    except InvalidSignature as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    event_id = body.get("event_id")
    if not event_id:
        raise HTTPException(status_code=400, detail="callback is missing event_id")

    try:
        order = confirm_payment(
            db,
            order_no=order_no,
            event_id=str(event_id),
            # Pass the raw value: the service enforces strict integer fen and
            # the server-authoritative amount; no float coercion here.
            paid_amount_fen=body["paid_amount_fen"],
        )
    except PaymentAmountMismatch as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid callback payload") from exc
    except InvalidOrderState as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OrderNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"order_no": order.no, "status": order.status, "event_id": event_id}


@router.post("/cancel")
def cancel(
    body: OrderNoIn,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    order = _load_owned(db, body.order_no, current_user.id)
    try:
        result = cancel_order(db, order.no)
    except InvalidOrderState as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"order_no": result.no, "status": result.status}
