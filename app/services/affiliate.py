"""Attribution, promoter/agent commission, agency fund and withdrawal service.

All money is integer 分. Every money mutation flushes inside the caller's
transaction (``confirm_payment`` / ``finish_refund`` own it) and carries a
deterministic idempotency key derived from the logical operation — never a
random default. The two order hooks keep the signatures T6 relies on.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.domain.attribution import (
    is_candidate_expired,
    is_confirmed,
    is_locked,
    utcnow,
)
from app.domain.commission import (
    ADMISSION_SKU,
    AGENCY_FUND_DEBIT_PERCENT,
    ATTRIBUTION_LOCKING_TYPES,
    INVITE_BONUS_CAP,
    INVITE_BONUS_CREDITS,
    NO_CASH_COMMISSION_SKUS,
    ACTIVE_ORDER_STATUSES,
    InsufficientAgentFund,
    compute_commission,
    is_agency_order,
    is_self_referral,
    is_valid_order,
)
from app.domain.ledger import append_entry
from app.models import (
    Agent,
    AgentFundEntry,
    Attribution,
    CommissionEntry,
    CreditEntry,
    Invitation,
    Order,
    Product,
    Promoter,
    User,
    Withdrawal,
)


# --- errors ------------------------------------------------------------------


class AffiliateError(Exception):
    """Base class for affiliate engine errors."""


class ReferrerNotFound(AffiliateError, LookupError):
    """The referral code does not resolve to a user."""


class AttributionNotFound(AffiliateError, LookupError):
    """No attribution candidate exists for the user."""


class AgentActivationError(AffiliateError, ValueError):
    """The order is not a paid agent-admission order."""


class WithdrawalError(AffiliateError, ValueError):
    """Base class for withdrawal validation errors."""


class WithdrawalNotFound(WithdrawalError, LookupError):
    """No withdrawal has the requested id."""


class InvalidWithdrawalAmount(WithdrawalError):
    """Amount is not a positive integer fen value."""


class BelowMinimumWithdrawal(WithdrawalError):
    """Amount is below MIN_WITHDRAW_FEN."""


class InsufficientWithdrawableBalance(WithdrawalError):
    """Amount exceeds matured, uncommitted promoter/agent commission."""


# --- attribution -------------------------------------------------------------


def _resolve_inviter(db: Session, code: str) -> User | None:
    """Resolve a code from invitation, promoter code, then user invite code."""
    invitation = db.query(Invitation).filter_by(code=code).first()
    if invitation is not None:
        return db.get(User, invitation.user_id)
    promoter = db.query(Promoter).filter_by(code=code).first()
    if promoter is not None:
        return db.get(User, promoter.user_id)
    return db.query(User).filter_by(invite_code=code).first()


def bind_referrer(db: Session, *, user_id: int, code: str) -> Attribution:
    """Bind a 90-day candidate attribution without ever overwriting a lock.

    While still a candidate the latest click wins (standard affiliate
    last-click behaviour); an expired candidate is reset to a fresh window.
    An explicit self-bind is allowed here — commission is suppressed later.
    """
    code = str(code).strip()
    inviter = _resolve_inviter(db, code)
    if inviter is None:
        raise ReferrerNotFound(f"unknown referral code: {code}")

    user_id = int(user_id)
    now = utcnow()
    attribution = db.query(Attribution).filter_by(user_id=user_id).first()
    if attribution is None:
        attribution = Attribution(
            user_id=user_id,
            inviter_id=inviter.id,
            code=code,
            candidate_at=now,
        )
        db.add(attribution)
    elif not is_locked(attribution):
        if is_candidate_expired(attribution.candidate_at, now):
            attribution.inviter_id = inviter.id
            attribution.code = code
            attribution.candidate_at = now
            attribution.confirmed_at = None
            attribution.promoter_id = None
            attribution.agent_id = None
        else:
            # Last click within the window; keep the original window start.
            attribution.inviter_id = inviter.id
            attribution.code = code
    db.flush()
    return attribution


def confirm_attribution(db: Session, *, user_id: int) -> Attribution:
    """Record the customer's explicit confirmation (idempotent)."""
    attribution = db.query(Attribution).filter_by(user_id=int(user_id)).first()
    if attribution is None:
        raise AttributionNotFound(f"no attribution for user {user_id}")
    if attribution.confirmed_at is None:
        attribution.confirmed_at = utcnow()
        db.flush()
    return attribution


def _resolve_roles_for_inviter(db: Session, inviter_id: int) -> tuple[int | None, int | None]:
    """Return (promoter_id, agent_id) for the referrer, one team level only.

    An active agent referrer takes the whole channel itself. A promoter
    referrer keeps its own commission and is linked to its direct upline agent
    through the shared ``org_id`` (the template's existing team linkage). A
    member who becomes an agent is handled independently at commission time.
    """
    agent = db.query(Agent).filter_by(user_id=inviter_id, status="active").first()
    if agent is not None:
        return None, agent.id

    promoter = db.query(Promoter).filter_by(user_id=inviter_id).first()
    if promoter is None:
        return None, None

    inviter = db.get(User, inviter_id)
    org_id = inviter.org_id if inviter is not None else None
    upline = None
    if org_id:
        upline = (
            db.query(Agent)
            .join(User, User.id == Agent.user_id)
            .filter(Agent.status == "active", User.org_id == org_id)
            .order_by(Agent.id)
            .first()
        )
    return promoter.id, (upline.id if upline is not None else None)


def _lock_attribution(db: Session, attribution: Attribution, order: Order) -> None:
    """Lock the long-term unique owner on the first valid package order."""
    if is_locked(attribution) or not is_valid_order(order):
        return
    # candidate_at is the authoritative 90-day clock; confirming does not reset
    # it, so a stale candidate can never acquire a long-term lock.
    if is_candidate_expired(attribution.candidate_at):
        return
    product = (
        db.get(Product, order.product_id)
        if order.product_id is not None
        else db.query(Product).filter_by(sku=order.sku).first()
    )
    if product is None or product.type not in ATTRIBUTION_LOCKING_TYPES:
        return
    attribution.locked_at = utcnow()
    if attribution.inviter_id is not None:
        (
            attribution.promoter_id,
            attribution.agent_id,
        ) = _resolve_roles_for_inviter(db, attribution.inviter_id)
    db.flush()


def _is_first_paid_order(db: Session, order: Order) -> bool:
    """First real order for the customer; ¥199 never counts as a 'first order'."""
    prior = (
        db.query(Order.id)
        .filter(Order.id != order.id)
        .filter(Order.user_id == order.user_id)
        .filter(Order.status.in_(ACTIVE_ORDER_STATUSES))
        .filter(Order.paid_amount_fen.isnot(None))
        .filter(~Order.sku.in_(NO_CASH_COMMISSION_SKUS))
        .filter(
            (Order.refund_status.is_(None))
            | (~Order.refund_status.in_(("refunding", "refunded")))
        )
        .first()
    )
    return prior is None


def _grant_invite_bonus(db: Session, attribution: Attribution, order: Order) -> None:
    """¥199 pays no cash: give the inviter 50 credits, capped at 20 invites."""
    if attribution.promoter_id is None:
        return
    promoter = db.get(Promoter, attribution.promoter_id)
    if promoter is None or promoter.invited_count >= INVITE_BONUS_CAP:
        return
    key = f"invite-bonus:{order.no}"
    if db.query(CreditEntry).filter_by(idempotency_key=key).first() is not None:
        return
    append_entry(
        db,
        user_id=promoter.user_id,
        delta=INVITE_BONUS_CREDITS,
        entry_type="grant",
        ref_type="invite_bonus",
        ref_id=order.no,
        idempotency_key=key,
    )
    promoter.invited_count += 1
    db.flush()


# --- commission --------------------------------------------------------------


def _available_at(order: Order):
    paid_at = order.paid_at or utcnow()
    return paid_at + timedelta(days=int(settings.commission_observation_days))


def _add_commission_entry(
    db: Session,
    *,
    user_id: int,
    order: Order,
    role: str,
    ref_id: int,
    amount_fen: int,
) -> CommissionEntry:
    key = f"commission:{role}:{order.no}"
    existing = db.query(CommissionEntry).filter_by(idempotency_key=key).first()
    if existing is not None:
        return existing
    entry = CommissionEntry(
        user_id=int(user_id),
        order_no=order.no,
        promoter_id=ref_id if role == "promoter" else None,
        agent_id=ref_id if role == "agent" else None,
        amount_fen=int(amount_fen),
        status="pending",
        available_at=_available_at(order),
        ref_type="order",
        ref_id=order.no,
        idempotency_key=key,
    )
    db.add(entry)
    return entry


def _agent_fund_balance(db: Session, agent_id: int) -> int:
    total = (
        db.query(func.coalesce(func.sum(AgentFundEntry.delta_fen), 0))
        .filter(AgentFundEntry.agent_id == agent_id)
        .scalar()
    )
    return int(total or 0)


def _debit_agency_fund(db: Session, order: Order) -> None:
    """代开: deduct 60% of the official price from the agent's fund."""
    agent = db.query(Agent).filter_by(user_id=order.user_id, status="active").first()
    if agent is None:
        return
    key = f"agent-fund-use:{order.no}"
    if db.query(AgentFundEntry).filter_by(idempotency_key=key).first() is not None:
        return
    product = (
        db.get(Product, order.product_id)
        if order.product_id is not None
        else db.query(Product).filter_by(sku=order.sku).first()
    )
    official = int(product.amount_fen) if product is not None else int(order.amount_fen)
    debit = -(official * AGENCY_FUND_DEBIT_PERCENT // 100)
    current = _agent_fund_balance(db, agent.id)
    balance = current + debit
    if balance < 0:
        # Never write a negative fund row; reject the order for top-up instead.
        raise InsufficientAgentFund(
            f"agent fund {current} fen cannot cover {abs(debit)} fen for {order.no}"
        )
    db.add(
        AgentFundEntry(
            agent_id=agent.id,
            user_id=order.user_id,
            delta_fen=debit,
            balance_after_fen=balance,
            ref_type="agency_order",
            ref_id=order.no,
            remark=f"代开扣款 {order.no}",
            idempotency_key=key,
        )
    )
    agent.fund_fen = balance
    db.flush()


def apply_order_commission(db: Session, *, order: Order) -> list[CommissionEntry]:
    """T6 hook: accrue promoter/agent commission on a fulfilled order.

    Returns the newly created (or previously existing) commission entries.
    Agency and agent-admission orders pay no commission; the ¥199 package
    locks attribution and grants invite credits but pays no cash. Expired or
    self-referred attributions earn nothing at all.
    """
    if is_agency_order(order):
        _debit_agency_fund(db, order)
        return []
    if order.sku == ADMISSION_SKU:
        activate_agent(db, user_id=order.user_id, payment_order_no=order.no)
        return []
    if not is_valid_order(order):
        return []

    attribution = db.query(Attribution).filter_by(user_id=order.user_id).first()
    if attribution is None or not is_confirmed(attribution):
        return []
    # candidate_at is the authoritative clock: a confirmed-but-stale candidate
    # can neither lock nor accrue (confirmation never resets the 90 days).
    if is_candidate_expired(attribution.candidate_at):
        return []
    _lock_attribution(db, attribution, order)
    if not is_locked(attribution):
        return []

    # Self-dealing is rejected before the ¥199 branch so it suppresses invite
    # credits as well as cash commission (and never consumes the 20-invite cap).
    customer = db.get(User, order.user_id)
    inviter = db.get(User, attribution.inviter_id) if attribution.inviter_id else None
    if is_self_referral(customer, inviter, settings.self_referral_check):
        return []

    if order.sku in NO_CASH_COMMISSION_SKUS:
        _grant_invite_bonus(db, attribution, order)
        return []

    # A member who becomes an agent is independent: no upline commission.
    promoter = db.get(Promoter, attribution.promoter_id) if attribution.promoter_id else None
    agent = db.get(Agent, attribution.agent_id) if attribution.agent_id else None
    if customer is not None and db.query(Agent).filter_by(
        user_id=customer.id, status="active"
    ).first() is not None:
        promoter = None
        agent = None

    base = int(order.paid_amount_fen or order.amount_fen or 0)
    split = compute_commission(
        base,
        promoter_level=promoter.level if promoter is not None else None,
        is_first_order=_is_first_paid_order(db, order),
        has_promoter=promoter is not None,
        has_agent=agent is not None,
    )

    entries: list[CommissionEntry] = []
    if promoter is not None and split.promoter_fen > 0:
        entries.append(
            _add_commission_entry(
                db,
                user_id=promoter.user_id,
                order=order,
                role="promoter",
                ref_id=promoter.id,
                amount_fen=split.promoter_fen,
            )
        )
    if agent is not None and split.agent_fen > 0:
        entries.append(
            _add_commission_entry(
                db,
                user_id=agent.user_id,
                order=order,
                role="agent",
                ref_id=agent.id,
                amount_fen=split.agent_fen,
            )
        )
    db.flush()
    return entries


def reverse_order_commission(db: Session, *, order: Order) -> list[CommissionEntry]:
    """T6 refund hook: mark this order's accruals reversed. Idempotent."""
    now = utcnow()
    reversed_entries: list[CommissionEntry] = []
    for entry in db.query(CommissionEntry).filter_by(order_no=order.no).all():
        if entry.status == "reversed" or entry.reversed_at is not None:
            continue
        entry.status = "reversed"
        entry.reversed_at = now
        reversed_entries.append(entry)
    db.flush()
    return reversed_entries


# --- agent admission ---------------------------------------------------------


def activate_agent(db: Session, *, user_id: int, payment_order_no: str) -> Agent:
    """Turn a paid ¥9999 admission order into non-withdrawable agent fund.

    Idempotent on ``agent-fund:{payment_order_no}``. The row may already exist
    in ``pending`` state because T5's ``apply_bonuses`` creates it at payment.
    """
    order = db.query(Order).filter_by(no=str(payment_order_no)).first()
    if order is None or order.sku != ADMISSION_SKU or order.paid_amount_fen is None:
        raise AgentActivationError(
            f"not a paid agent-admission order: {payment_order_no}"
        )
    if order.status in ("refunding", "refunded"):
        raise AgentActivationError(f"agent admission order was refunded: {order.no}")

    user_id = int(user_id)
    agent = db.query(Agent).filter_by(user_id=user_id).first()
    if agent is None:
        agent = Agent(user_id=user_id, status="pending")
        db.add(agent)
        db.flush()

    key = f"agent-fund:{order.no}"
    if db.query(AgentFundEntry).filter_by(idempotency_key=key).first() is not None:
        return agent

    amount = int(order.paid_amount_fen)
    balance = _agent_fund_balance(db, agent.id) + amount
    db.add(
        AgentFundEntry(
            agent_id=agent.id,
            user_id=user_id,
            delta_fen=amount,
            balance_after_fen=balance,
            ref_type="admission",
            ref_id=order.no,
            remark="agent admission fund (non-withdrawable)",
            idempotency_key=key,
        )
    )
    agent.status = "active"
    agent.fund_fen = balance
    agent.approved_at = utcnow()
    agent.admission_order_no = order.no
    user = db.get(User, user_id)
    if user is not None:
        user.role = "agent"
    db.flush()
    return agent


# --- withdrawals -------------------------------------------------------------


def total_commission(db: Session, *, user_id: int) -> int:
    """All non-reversed pre-tax commission accruals for the user, in 分."""
    total = (
        db.query(func.coalesce(func.sum(CommissionEntry.amount_fen), 0))
        .filter(
            CommissionEntry.user_id == int(user_id),
            CommissionEntry.status != "reversed",
            CommissionEntry.reversed_at.is_(None),
        )
        .scalar()
    )
    return int(total or 0)


def withdrawable_balance(db: Session, *, user_id: int) -> int:
    """Matured commission minus pending/approved withdrawals (agent fund excluded)."""
    user_id = int(user_id)
    matured = (
        db.query(func.coalesce(func.sum(CommissionEntry.amount_fen), 0))
        .filter(
            CommissionEntry.user_id == user_id,
            CommissionEntry.status == "pending",
            CommissionEntry.reversed_at.is_(None),
            CommissionEntry.available_at.isnot(None),
            CommissionEntry.available_at <= utcnow(),
        )
        .scalar()
    )
    committed = (
        db.query(func.coalesce(func.sum(Withdrawal.amount_fen), 0))
        .filter(
            Withdrawal.user_id == user_id,
            Withdrawal.status.in_(("pending", "approved", "paid")),
        )
        .scalar()
    )
    return max(0, int(matured or 0) - int(committed or 0))


def request_withdrawal(db: Session, *, user_id: int, amount_fen: int) -> Withdrawal:
    """Create a pending withdrawal request after balance / observation checks.

    A same-day repeat of the same amount reuses the deterministic key, so a
    retried submission can never create a second request.
    """
    if isinstance(amount_fen, bool) or not isinstance(amount_fen, int):
        raise InvalidWithdrawalAmount("amount_fen must be an integer in fen")
    if amount_fen <= 0:
        raise InvalidWithdrawalAmount("amount_fen must be positive")
    minimum = int(settings.min_withdraw_fen)
    if amount_fen < minimum:
        raise BelowMinimumWithdrawal(f"minimum withdrawal is {minimum} fen")
    if amount_fen > withdrawable_balance(db, user_id=int(user_id)):
        raise InsufficientWithdrawableBalance("no matured commission available")

    key = f"withdraw:{int(user_id)}:{amount_fen}:{utcnow().date().isoformat()}"
    existing = db.query(Withdrawal).filter_by(idempotency_key=key).first()
    if existing is not None:
        return existing
    withdrawal = Withdrawal(
        user_id=int(user_id),
        amount_fen=amount_fen,
        status="pending",
        idempotency_key=key,
    )
    db.add(withdrawal)
    db.flush()
    return withdrawal


def approve_withdrawal(db: Session, *, withdrawal_id: int, approved: bool) -> Withdrawal:
    """Mark a withdrawal approved/rejected. No real payout is performed here.

    T9's admin and the external payout system own the actual bank/WeChat
    transfer; this call only records the review decision (idempotently).
    """
    withdrawal = db.get(Withdrawal, int(withdrawal_id))
    if withdrawal is None:
        raise WithdrawalNotFound(f"unknown withdrawal: {withdrawal_id}")
    if withdrawal.status in ("approved", "rejected", "paid"):
        return withdrawal
    withdrawal.status = "approved" if approved else "rejected"
    withdrawal.reviewed_at = utcnow()
    db.flush()
    return withdrawal
