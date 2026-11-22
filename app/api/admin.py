"""Admin console: role-gated JSON endpoints and Jinja2 pages for operations.

Every write funnels through :func:`_audit` so the trail carries the operator,
action, before/after values, reason and timestamp. Money mutations use
deterministic idempotency keys and commit in one transaction. Refund approval
delegates to T6's ``finish_refund`` so commission reversal, entitlement
revocation and unused-credit refund side effects stay intact.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.adapters.payment import get_gateway
from app.db import get_db
from app.domain.ledger import InsufficientCredits, append_entry
from app.models import (
    Agent,
    AgentFundEntry,
    AuditLog,
    CommissionEntry,
    CreditEntry,
    Order,
    Product,
    User,
    Withdrawal,
)
from app.models.base import utcnow
from app.services.admin import (
    agent_view as _agent_view,
    compute_stats as _stats,
    deterministic_key as _key,
    order_view as _order_view,
    product_view as _product_view,
    record_audit,
    withdrawal_view as _withdrawal_view,
)
from app.services.affiliate import WithdrawalError, approve_withdrawal
from app.services.auth import require_roles
from app.services.credits import get_balance
from app.services.orders import OrderError, begin_refund, confirm_payment, finish_refund

router = APIRouter(prefix="/admin", tags=["admin"])
pages_router = APIRouter(tags=["admin-pages"])
_templates = Jinja2Templates(directory="templates")
admin_required = require_roles("admin")


# --- thin API-layer adapters over the pure admin service ---------------------


def _nonempty_reason(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("reason must be a non-empty string")
    return value.strip()


def _audit(
    db: Session,
    *,
    actor: User,
    action: str,
    target_type: str,
    target_id: object,
    before: dict | None,
    after: dict | None,
    reason: str | None,
    request: Request | None = None,
) -> AuditLog:
    return record_audit(
        db,
        actor_id=actor.id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before=before,
        after=after,
        reason=reason,
        ip=request.client.host if (request is not None and request.client) else None,
    )


# --- request bodies ----------------------------------------------------------


class CreditAdjustIn(BaseModel):
    user_id: int
    delta: int
    reason: str
    idempotency_key: str | None = None

    @field_validator("reason")
    @classmethod
    def _reason_required(cls, value: str) -> str:
        return _nonempty_reason(value)


class ProductUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    type: str | None = None
    amount_fen: int | None = None
    original_amount_fen: int | None = None
    credits: int | None = None
    bonuses: dict | None = None
    eligibility: dict | None = None
    active: bool | None = None
    sort: int | None = None
    reason: str | None = None


class SyncIn(BaseModel):
    reason: str | None = None


class RefundIn(BaseModel):
    amount_fen: int | None = None
    reason: str | None = None


class AgentReviewIn(BaseModel):
    approved: bool
    reason: str | None = None


class FundAdjustIn(BaseModel):
    delta_fen: int
    reason: str
    idempotency_key: str | None = None

    @field_validator("reason")
    @classmethod
    def _reason_required(cls, value: str) -> str:
        return _nonempty_reason(value)


class WithdrawalReviewIn(BaseModel):
    approved: bool
    reason: str | None = None


# --- stats / audit -----------------------------------------------------------


@router.get("/stats")
def stats(actor: User = Depends(admin_required), db: Session = Depends(get_db)):
    return _stats(db)


@router.get("/audit-logs")
def audit_logs(
    limit: int = 100,
    actor: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(AuditLog)
        .order_by(AuditLog.id.desc())
        .limit(min(max(limit, 1), 500))
        .all()
    )
    return [
        {
            "id": row.id,
            "actor_id": row.actor_id,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "before": row.before,
            "after": row.after,
            "reason": row.reason,
            "created_at": row.created_at,
        }
        for row in rows
    ]


# --- product config ----------------------------------------------------------


@router.get("/products")
def products(actor: User = Depends(admin_required), db: Session = Depends(get_db)):
    return [_product_view(p) for p in db.query(Product).order_by(Product.sort, Product.id).all()]


@router.patch("/products/{sku}")
def update_product(
    sku: str,
    body: ProductUpdateIn,
    request: Request,
    actor: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    product = db.query(Product).filter_by(sku=str(sku)).first()
    if product is None:
        raise HTTPException(status_code=404, detail=f"unknown product: {sku}")
    before = _product_view(product)
    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    for field, value in changes.items():
        setattr(product, field, value)
    db.flush()
    _audit(
        db,
        actor=actor,
        action="admin:product_update",
        target_type="product",
        target_id=product.id,
        before=before,
        after=_product_view(product),
        reason=body.reason,
        request=request,
    )
    db.commit()
    return _product_view(product)


# --- orders ------------------------------------------------------------------


@router.get("/orders")
def orders(
    status: str | None = None,
    limit: int = 100,
    actor: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    query = db.query(Order)
    if status:
        query = query.filter(Order.status == status)
    rows = query.order_by(Order.id.desc()).limit(min(max(limit, 1), 500)).all()
    return [_order_view(o) for o in rows]


@router.post("/orders/{order_no}/sync")
def sync_order(
    order_no: str,
    request: Request,
    body: SyncIn | None = None,
    actor: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    """Query the gateway and fulfil a paid-but-unconfirmed order manually."""
    order = db.query(Order).filter_by(no=str(order_no)).first()
    if order is None:
        raise HTTPException(status_code=404, detail=f"unknown order: {order_no}")
    before = _order_view(order)
    if get_gateway().query(order.no) == "paid" and order.status in (
        "created", "pending_payment", "paid"
    ):
        try:
            confirm_payment(
                db,
                order_no=order.no,
                event_id=f"manual-sync:{order.no}",
                paid_amount_fen=int(order.amount_fen),
            )
        except OrderError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.refresh(order)
    after = _order_view(order)
    if after["status"] != before["status"]:
        _audit(
            db,
            actor=actor,
            action="admin:order_sync",
            target_type="order",
            target_id=order.no,
            before=before,
            after=after,
            reason=(body.reason if body else None) or "manual gateway sync",
            request=request,
        )
        db.commit()
    return after


@router.post("/orders/{order_no}/refund")
def approve_refund(
    order_no: str,
    request: Request,
    body: RefundIn | None = None,
    actor: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    """Approve a refund; T6's service owns every financial side effect."""
    order = db.query(Order).filter_by(no=str(order_no)).first()
    if order is None:
        raise HTTPException(status_code=404, detail=f"unknown order: {order_no}")
    before = _order_view(order)
    if before["status"] == "refunded":
        return before  # idempotent replay: no second audit row

    if before["status"] not in ("refunding", "refunded"):
        try:
            begin_refund(db, order.no)
        except OrderError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    amount = (body.amount_fen if body else None) or order.paid_amount_fen
    try:
        finish_refund(db, order.no, amount_fen=amount)
    except OrderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    db.refresh(order)
    after = _order_view(order)
    _audit(
        db,
        actor=actor,
        action="admin:refund_approve",
        target_type="order",
        target_id=order.no,
        before=before,
        after=after,
        reason=(body.reason if body else None),
        request=request,
    )
    db.commit()
    return after


# --- credit adjustment -------------------------------------------------------


@router.post("/credits/adjust")
def adjust_credits(
    body: CreditAdjustIn,
    request: Request,
    actor: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    user = db.get(User, int(body.user_id))
    if user is None:
        raise HTTPException(status_code=404, detail=f"unknown user: {body.user_id}")
    key = body.idempotency_key or _key(
        "admin-credit-adjust", body.user_id, body.delta, body.reason
    )
    existing = db.query(CreditEntry).filter_by(idempotency_key=key).first()
    if existing is not None:
        return {
            "user_id": body.user_id,
            "delta": body.delta,
            "balance": get_balance(db, body.user_id),
            "idempotency_key": key,
            "replay": True,
        }

    before_balance = get_balance(db, body.user_id)
    try:
        append_entry(
            db,
            user_id=body.user_id,
            delta=body.delta,
            entry_type="admin_adjust",
            ref_type="admin",
            ref_id=str(actor.id),
            idempotency_key=key,
        )
    except InsufficientCredits as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    after_balance = get_balance(db, body.user_id)
    _audit(
        db,
        actor=actor,
        action="admin:credit_adjust",
        target_type="user",
        target_id=body.user_id,
        before={"balance": before_balance},
        after={"balance": after_balance, "delta": body.delta, "entry_type": "admin_adjust"},
        reason=body.reason,
        request=request,
    )
    db.commit()
    return {
        "user_id": body.user_id,
        "delta": body.delta,
        "balance": after_balance,
        "idempotency_key": key,
        "replay": False,
    }


# --- agent review / manual fund settlement -----------------------------------


@router.get("/agents")
def agents(actor: User = Depends(admin_required), db: Session = Depends(get_db)):
    return [_agent_view(a) for a in db.query(Agent).order_by(Agent.id).all()]


@router.post("/agents/{agent_id}/review")
def review_agent(
    agent_id: int,
    body: AgentReviewIn,
    request: Request,
    actor: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    agent = db.get(Agent, int(agent_id))
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_id}")
    before = _agent_view(agent)
    new_status = "active" if body.approved else "rejected"
    agent.status = new_status
    if body.approved:
        if agent.approved_at is None:
            agent.approved_at = utcnow()
        user = db.get(User, agent.user_id)
        if user is not None:
            user.role = "agent"
    db.flush()
    _audit(
        db,
        actor=actor,
        action="admin:agent_review",
        target_type="agent",
        target_id=agent.id,
        before=before,
        after=_agent_view(agent),
        reason=body.reason,
        request=request,
    )
    db.commit()
    return _agent_view(agent)


@router.post("/agents/{agent_id}/fund/adjust")
def adjust_agent_fund(
    agent_id: int,
    body: FundAdjustIn,
    request: Request,
    actor: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    """Manual agent-fund settlement (e.g. admission-refund clawback), with audit."""
    agent = db.get(Agent, int(agent_id))
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_id}")
    key = body.idempotency_key or _key(
        "admin-fund-adjust", agent.id, body.delta_fen, body.reason
    )
    if db.query(AgentFundEntry).filter_by(idempotency_key=key).first() is not None:
        db.refresh(agent)
        return _agent_view(agent)

    current = int(
        db.query(func.coalesce(func.sum(AgentFundEntry.delta_fen), 0))
        .filter(AgentFundEntry.agent_id == agent.id)
        .scalar() or 0
    )
    balance = current + int(body.delta_fen)
    if balance < 0:
        raise HTTPException(
            status_code=400,
            detail=f"agent fund {current} fen cannot cover a {body.delta_fen} fen debit",
        )
    before = _agent_view(agent)
    db.add(
        AgentFundEntry(
            agent_id=agent.id,
            user_id=agent.user_id,
            delta_fen=int(body.delta_fen),
            balance_after_fen=balance,
            ref_type="admin_adjustment",
            ref_id=key,
            remark=body.reason,
            idempotency_key=key,
        )
    )
    agent.fund_fen = balance
    db.flush()
    _audit(
        db,
        actor=actor,
        action="admin:agent_fund_adjust",
        target_type="agent",
        target_id=agent.id,
        before=before,
        after=_agent_view(agent),
        reason=body.reason,
        request=request,
    )
    db.commit()
    return _agent_view(agent)


# --- commission / withdrawal review -----------------------------------------


@router.get("/commissions")
def commissions(actor: User = Depends(admin_required), db: Session = Depends(get_db)):
    rows = db.query(CommissionEntry).order_by(CommissionEntry.id).all()
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "order_no": row.order_no,
            "amount_fen": row.amount_fen,
            "status": row.status,
            "available_at": row.available_at,
            "reversed_at": row.reversed_at,
        }
        for row in rows
    ]


@router.get("/withdrawals")
def withdrawals(actor: User = Depends(admin_required), db: Session = Depends(get_db)):
    return [
        _withdrawal_view(w)
        for w in db.query(Withdrawal).order_by(Withdrawal.id).all()
    ]


@router.post("/withdrawals/{withdrawal_id}/review")
def review_withdrawal(
    withdrawal_id: int,
    body: WithdrawalReviewIn,
    request: Request,
    actor: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    withdrawal = db.get(Withdrawal, int(withdrawal_id))
    if withdrawal is None:
        raise HTTPException(status_code=404, detail=f"unknown withdrawal: {withdrawal_id}")
    before = _withdrawal_view(withdrawal)
    try:
        approve_withdrawal(db, withdrawal_id=withdrawal.id, approved=body.approved)
    except WithdrawalError as exc:
        status = 404 if isinstance(exc, LookupError) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    db.refresh(withdrawal)
    after = _withdrawal_view(withdrawal)
    if after["status"] != before["status"]:
        _audit(
            db,
            actor=actor,
            action="admin:withdrawal_review",
            target_type="withdrawal",
            target_id=withdrawal.id,
            before=before,
            after=after,
            reason=body.reason,
            request=request,
        )
    db.commit()
    return after


# --- HTML pages --------------------------------------------------------------


@pages_router.get("/admin", response_class=HTMLResponse)
def admin_home(
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(admin_required),
):
    return _templates.TemplateResponse(
        request=request,
        name="admin/dashboard.html",
        context={"stats": _stats(db), "user": actor},
    )


@pages_router.get("/admin/ui/orders", response_class=HTMLResponse)
def admin_orders_page(
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(admin_required),
):
    rows = db.query(Order).order_by(Order.id.desc()).limit(100).all()
    return _templates.TemplateResponse(
        request=request,
        name="admin/orders.html",
        context={"orders": [_order_view(o) for o in rows]},
    )


@pages_router.get("/admin/ui/products", response_class=HTMLResponse)
def admin_products_page(
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(admin_required),
):
    rows = db.query(Product).order_by(Product.sort, Product.id).all()
    return _templates.TemplateResponse(
        request=request,
        name="admin/products.html",
        context={"products": [_product_view(p) for p in rows]},
    )


@pages_router.get("/admin/ui/withdrawals", response_class=HTMLResponse)
def admin_withdrawals_page(
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(admin_required),
):
    rows = db.query(Withdrawal).order_by(Withdrawal.id).all()
    return _templates.TemplateResponse(
        request=request,
        name="admin/withdrawals.html",
        context={"withdrawals": [_withdrawal_view(w) for w in rows]},
    )
