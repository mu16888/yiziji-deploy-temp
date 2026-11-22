"""Admin-support helpers: audit recording, snapshots and dashboard stats.

Pure service layer — no FastAPI imports. The API layer passes the operator id
and (optionally) the client IP; this module never depends on HTTP objects.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime

from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app.models import (
    Agent,
    AuditLog,
    CommissionEntry,
    CreditEntry,
    Order,
    Product,
    User,
    Withdrawal,
)


def jsonable(value):
    """Make snapshot dicts safe for the audit table's JSON column."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def deterministic_key(prefix: str, *parts: object) -> str:
    """Derive an idempotency key from the logical operation, never a random UUID."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]
    return f"{prefix}:{digest}"


def record_audit(
    db: Session,
    *,
    actor_id: int,
    action: str,
    target_type: str,
    target_id: object,
    before: dict | None,
    after: dict | None,
    reason: str | None,
    ip: str | None = None,
) -> AuditLog:
    """Append one audit row (operator, action, before/after, reason, timestamp)."""
    row = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        before=jsonable(before),
        after=jsonable(after),
        reason=reason or action,
        ip=ip,
    )
    db.add(row)
    return row


def product_view(p: Product) -> dict:
    return {
        "id": p.id,
        "sku": p.sku,
        "name": p.name,
        "description": p.description,
        "type": p.type,
        "amount_fen": p.amount_fen,
        "original_amount_fen": p.original_amount_fen,
        "credits": p.credits,
        "bonuses": p.bonuses,
        "eligibility": p.eligibility,
        "active": p.active,
        "sort": p.sort,
    }


def order_view(o: Order) -> dict:
    return {
        "id": o.id,
        "no": o.no,
        "user_id": o.user_id,
        "sku": o.sku,
        "amount_fen": o.amount_fen,
        "paid_amount_fen": o.paid_amount_fen,
        "status": o.status,
        "channel": o.channel,
        "refund_status": o.refund_status,
        "paid_at": o.paid_at,
        "fulfilled_at": o.fulfilled_at,
    }


def agent_view(a: Agent) -> dict:
    return {
        "id": a.id,
        "user_id": a.user_id,
        "status": a.status,
        "fund_fen": a.fund_fen,
        "admission_order_no": a.admission_order_no,
        "approved_at": a.approved_at,
    }


def withdrawal_view(w: Withdrawal) -> dict:
    return {
        "id": w.id,
        "user_id": w.user_id,
        "amount_fen": w.amount_fen,
        "status": w.status,
        "reviewed_by": w.reviewed_by,
        "reviewed_at": w.reviewed_at,
    }


def compute_stats(db: Session) -> dict:
    """Registration / paid volume / credits consumed / unsettled commission."""
    paid_filter = (
        Order.status.in_(("paid", "fulfilled"))
        & Order.paid_amount_fen.isnot(None)
        & (Order.refund_status.is_(None) | ~Order.refund_status.in_(("refunding", "refunded")))
    )
    registered = db.query(func.count(User.id)).scalar() or 0
    paid_amount = db.query(func.coalesce(func.sum(Order.paid_amount_fen), 0)).filter(
        paid_filter
    ).scalar() or 0
    paid_users = db.query(func.count(distinct(Order.user_id))).filter(paid_filter).scalar() or 0
    consumed = db.query(func.coalesce(func.sum(-CreditEntry.delta), 0)).filter(
        CreditEntry.delta < 0,
        CreditEntry.entry_type.in_(("consume", "settle")),
    ).scalar() or 0
    pending_commission = db.query(
        func.coalesce(func.sum(CommissionEntry.amount_fen), 0)
    ).filter(
        CommissionEntry.status == "pending",
        CommissionEntry.reversed_at.is_(None),
    ).scalar() or 0
    pending_withdrawals = db.query(func.count(Withdrawal.id)).filter_by(
        status="pending"
    ).scalar() or 0
    return {
        "registered_users": int(registered),
        "paid_users": int(paid_users),
        "paid_amount_fen": int(paid_amount),
        "credits_consumed": int(consumed),
        "pending_commission_fen": int(pending_commission),
        "pending_withdrawals": int(pending_withdrawals),
    }
