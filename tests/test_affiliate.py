"""Attribution, promoter/agent commission, agent fund and withdrawal (Task 8).

The four tests named in the brief are kept verbatim by name. Every money test
uses the shared in-memory fixture; all amounts are integer 分.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import (
    Agent,
    AgentFundEntry,
    Attribution,
    CommissionEntry,
    CreditEntry,
    Order,
    Product,
    Promoter,
    User,
    Withdrawal,
)
from app.models.base import utcnow
from app.services.auth import issue_token
from app.services.affiliate import (
    AgentActivationError,
    BelowMinimumWithdrawal,
    InsufficientAgentFund,
    InsufficientWithdrawableBalance,
    ReferrerNotFound,
    activate_agent,
    approve_withdrawal,
    apply_order_commission,
    bind_referrer,
    confirm_attribution,
    request_withdrawal,
    reverse_order_commission,
)
from app.services.orders import begin_refund, confirm_payment, create_order, finish_refund
from scripts.seed import PRODUCTS

PERSONAL = "pkg_personal_699"
NEWCOMER = "pkg_newcomer_experience_199"
ADMISSION = "agent_admission_9999"


# --- fixtures / helpers ------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    for data in PRODUCTS:
        session.add(Product(**data))
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


_seq = {"n": 0}


def make_user(db, **kwargs):
    _seq["n"] += 1
    n = _seq["n"]
    defaults = {
        "phone": f"139{10000000 + n:08d}",
        "display_name": f"U{n}",
        "is_active": True,
        "role": "user",
    }
    defaults.update(kwargs)
    user = User(**defaults)
    db.add(user)
    db.flush()
    return user


def make_promoter(db, *, level="basic", code="TCH-8F3K", **user_kwargs):
    user = make_user(db, role="promoter", invite_code=code, **user_kwargs)
    promoter = Promoter(user_id=user.id, level=level, code=code)
    db.add(promoter)
    db.flush()
    return user, promoter


def make_agent(db, *, org_id=None, status="active", fund_fen=0, **user_kwargs):
    user = make_user(db, role="agent", org_id=org_id, **user_kwargs)
    agent = Agent(user_id=user.id, status=status, fund_fen=fund_fen, approved_at=utcnow())
    db.add(agent)
    db.flush()
    if fund_fen:
        # Money always lives in the ledger; the column is a cached snapshot.
        db.add(
            AgentFundEntry(
                agent_id=agent.id,
                user_id=user.id,
                delta_fen=fund_fen,
                balance_after_fen=fund_fen,
                ref_type="seed",
                ref_id=f"seed:{agent.id}",
                idempotency_key=f"agent-fund-seed:{agent.id}",
            )
        )
        db.flush()
    return user, agent


def paid_order(db, user, sku, *, event_id="e1", channel="native"):
    order = create_order(db, user_id=user.id, sku=sku, channel=channel)
    confirm_payment(
        db, order_no=order.no, event_id=event_id, paid_amount_fen=order.amount_fen
    )
    return order


def auth_headers(user):
    return {"Authorization": f"Bearer {issue_token(user, channel='web')}"}


def commission_for_order(db, order_no):
    return db.query(CommissionEntry).filter_by(order_no=order_no).all()


# --- the brief's four named tests -------------------------------------------


def test_attribution_locks_after_first_valid_order(db):
    promo_user, promo = make_promoter(db, code="TCH-8F3K", level="basic")
    customer = make_user(db)

    attribution = bind_referrer(db, user_id=customer.id, code="TCH-8F3K")
    assert attribution.inviter_id == promo_user.id
    assert attribution.locked_at is None
    confirmed = confirm_attribution(db, user_id=customer.id)
    assert confirmed.confirmed_at is not None

    # ¥199 locks attribution but pays NO cash commission.
    newcomer = paid_order(db, customer, NEWCOMER)
    locked = db.query(Attribution).filter_by(user_id=customer.id).one()
    assert locked.locked_at is not None
    assert locked.promoter_id == promo.id
    assert commission_for_order(db, newcomer.no) == []

    # A later ¥699 order pays the promoter 20% (199 never counts as a "first
    # order" for cash-rate purposes).
    order = paid_order(db, customer, PERSONAL, event_id="e2")
    entries = commission_for_order(db, order.no)
    assert len(entries) == 1
    assert entries[0].user_id == promo_user.id
    assert entries[0].promoter_id == promo.id
    assert entries[0].amount_fen == 13980  # 69900 * 20%


def test_channel_cap_and_team_difference(db):
    agent_user, agent = make_agent(db, org_id="ORG-TEAM", fund_fen=999900)
    promo_user, promo = make_promoter(db, code="TCH-BASIC", level="basic", org_id="ORG-TEAM")
    customer = make_user(db)
    bind_referrer(db, user_id=customer.id, code="TCH-BASIC")
    confirm_attribution(db, user_id=customer.id)

    order = paid_order(db, customer, PERSONAL)
    entries = {e.promoter_id: e for e in commission_for_order(db, order.no)}
    promoter_entry = next(e for e in entries.values() if e.promoter_id == promo.id)
    agent_entry = next(e for e in entries.values() if e.agent_id == agent.id)
    assert promoter_entry.amount_fen == 13980  # 69900 * 20%
    assert agent_entry.amount_fen == 13980  # 40% - 20% = 20%
    assert sum(e.amount_fen for e in entries.values()) <= 69900 * 40 // 100

    # Advanced promoter: first order 30%, agent keeps 40% - 30% = 10%.
    _, advanced = make_promoter(db, code="TCH-ADV", level="advanced", org_id="ORG-TEAM")
    buyer2 = make_user(db)
    bind_referrer(db, user_id=buyer2.id, code="TCH-ADV")
    confirm_attribution(db, user_id=buyer2.id)
    order2 = paid_order(db, buyer2, PERSONAL, event_id="e2")
    entries2 = commission_for_order(db, order2.no)
    adv_entry = next(e for e in entries2 if e.promoter_id == advanced.id)
    ag_entry = next(e for e in entries2 if e.agent_id == agent.id)
    assert adv_entry.amount_fen == 20970  # 69900 * 30%
    assert ag_entry.amount_fen == 6990  # 69900 * (40% - 30%)
    assert sum(e.amount_fen for e in entries2) <= 69900 * 40 // 100


def test_self_referral_no_commission(db, monkeypatch):
    # Own invite code is always self-dealing, regardless of the config switch.
    self_user = make_user(db, invite_code="TCH-SELF")
    db.add(Promoter(user_id=self_user.id, level="basic", code="TCH-SELF"))
    db.flush()
    bind_referrer(db, user_id=self_user.id, code="TCH-SELF")
    confirm_attribution(db, user_id=self_user.id)
    self_order = paid_order(db, self_user, PERSONAL)
    assert commission_for_order(db, self_order.no) == []

    # Same org_id / same identity_hash are config-gated signals.
    monkeypatch.setattr(settings, "self_referral_check", "both")
    inviter, _ = make_promoter(db, code="TCH-ORGINV", org_id="ORG-SAME")
    buyer = make_user(db, org_id="ORG-SAME")
    bind_referrer(db, user_id=buyer.id, code="TCH-ORGINV")
    confirm_attribution(db, user_id=buyer.id)
    org_order = paid_order(db, buyer, PERSONAL, event_id="e2")
    assert commission_for_order(db, org_order.no) == []

    monkeypatch.setattr(settings, "self_referral_check", "identity")
    id_inviter, _ = make_promoter(db, code="TCH-IDSAME", identity_hash="IDHASH-A")
    id_buyer = make_user(db, identity_hash="IDHASH-A")
    bind_referrer(db, user_id=id_buyer.id, code="TCH-IDSAME")
    confirm_attribution(db, user_id=id_buyer.id)
    id_order = paid_order(db, id_buyer, PERSONAL, event_id="e3")
    assert commission_for_order(db, id_order.no) == []


def test_observation_period_and_withdrawal_minimum(db):
    promo_user, _ = make_promoter(db, code="TCH-WITHDRAW")
    customer = make_user(db)
    bind_referrer(db, user_id=customer.id, code="TCH-WITHDRAW")
    confirm_attribution(db, user_id=customer.id)
    order = paid_order(db, customer, PERSONAL)

    entry = commission_for_order(db, order.no)[0]
    assert entry.available_at == order.paid_at + timedelta(
        days=settings.commission_observation_days
    )

    with pytest.raises(BelowMinimumWithdrawal):
        request_withdrawal(db, user_id=promo_user.id, amount_fen=9999)

    # Still inside the 15-day observation window -> nothing withdrawable.
    with pytest.raises(InsufficientWithdrawableBalance):
        request_withdrawal(db, user_id=promo_user.id, amount_fen=10000)

    # Simulate the observation window having elapsed.
    entry.available_at = order.paid_at - timedelta(days=1)
    db.commit()
    withdrawal = request_withdrawal(db, user_id=promo_user.id, amount_fen=10000)
    assert withdrawal.status == "pending"
    assert withdrawal.amount_fen == 10000

    # The pending request already reserves part of the matured balance.
    with pytest.raises(InsufficientWithdrawableBalance):
        request_withdrawal(db, user_id=promo_user.id, amount_fen=10000)

    approved = approve_withdrawal(db, withdrawal_id=withdrawal.id, approved=True)
    assert approved.status == "approved" and approved.reviewed_at is not None
    assert approve_withdrawal(db, withdrawal_id=withdrawal.id, approved=True).status == "approved"


def test_approve_withdrawal_reject_path(db):
    promo_user, _ = make_promoter(db, code="TCH-REJECT")
    for n in range(2):
        customer = make_user(db)
        bind_referrer(db, user_id=customer.id, code="TCH-REJECT")
        confirm_attribution(db, user_id=customer.id)
        order = paid_order(db, customer, PERSONAL, event_id=f"e{n}")
        entry = commission_for_order(db, order.no)[0]
        entry.available_at = order.paid_at - timedelta(days=1)
    db.commit()

    withdrawal = request_withdrawal(db, user_id=promo_user.id, amount_fen=10000)
    rejected = approve_withdrawal(db, withdrawal_id=withdrawal.id, approved=False)
    assert rejected.status == "rejected" and rejected.reviewed_at is not None
    assert approve_withdrawal(db, withdrawal_id=withdrawal.id, approved=False).status == "rejected"


# --- additional coverage ----------------------------------------------------


def test_reverse_order_commission_on_refund(db):
    promo_user, _ = make_promoter(db, code="TCH-REVERSE")
    customer = make_user(db)
    bind_referrer(db, user_id=customer.id, code="TCH-REVERSE")
    confirm_attribution(db, user_id=customer.id)
    order = paid_order(db, customer, PERSONAL)
    assert len(commission_for_order(db, order.no)) == 1

    begin_refund(db, order.no)
    finish_refund(db, order.no)
    entry = commission_for_order(db, order.no)[0]
    assert entry.status == "reversed" and entry.reversed_at is not None

    # A repeated reversal is a no-op.
    reverse_order_commission(db, order=order)
    assert len(commission_for_order(db, order.no)) == 1


def test_199_invite_bonus_credits_capped_at_20(db):
    promo_user, _ = make_promoter(db, code="TCH-INVITE")
    for n in range(21):
        customer = make_user(db)
        bind_referrer(db, user_id=customer.id, code="TCH-INVITE")
        confirm_attribution(db, user_id=customer.id)
        paid_order(db, customer, NEWCOMER, event_id=f"e{n}")

    promoter = db.query(Promoter).filter_by(user_id=promo_user.id).one()
    assert promoter.invited_count == 20
    bonus = (
        db.query(CreditEntry)
        .filter_by(user_id=promo_user.id, entry_type="grant", ref_type="invite_bonus")
        .count()
    )
    assert bonus == 20


def test_activate_agent_fund_is_non_withdrawable(db):
    user = make_user(db)
    order = paid_order(db, user, ADMISSION)
    # The order hook activates the agent on fulfilment; calling the public
    # interface again must stay idempotent.
    assert db.query(Agent).filter_by(user_id=user.id).one().status == "active"

    activated = activate_agent(db, user_id=user.id, payment_order_no=order.no)
    assert activated.status == "active"
    assert activated.fund_fen == 999900
    assert activated.admission_order_no == order.no
    assert db.get(User, user.id).role == "agent"

    fund_entry = db.query(AgentFundEntry).filter_by(agent_id=activated.id).one()
    assert fund_entry.delta_fen == 999900
    assert fund_entry.balance_after_fen == 999900
    assert fund_entry.idempotency_key == f"agent-fund:{order.no}"

    assert activate_agent(db, user_id=user.id, payment_order_no=order.no).id == activated.id
    assert db.query(AgentFundEntry).count() == 1

    # The agent fund is never withdrawable cash.
    with pytest.raises(InsufficientWithdrawableBalance):
        request_withdrawal(db, user_id=user.id, amount_fen=10000)


def test_activate_agent_rejects_non_admission_order(db):
    user = make_user(db)
    order = paid_order(db, user, PERSONAL)
    with pytest.raises(AgentActivationError):
        activate_agent(db, user_id=user.id, payment_order_no=order.no)


def test_bind_referrer_unknown_code_and_locked_attribution(db):
    with pytest.raises(ReferrerNotFound):
        bind_referrer(db, user_id=1, code="TCH-NOPE")

    promo_user, _ = make_promoter(db, code="TCH-LOCK")
    customer = make_user(db)
    bind_referrer(db, user_id=customer.id, code="TCH-LOCK")
    confirm_attribution(db, user_id=customer.id)
    paid_order(db, customer, PERSONAL)
    other_user, _ = make_promoter(db, code="TCH-POACH")
    locked = bind_referrer(db, user_id=customer.id, code="TCH-POACH")
    assert locked.inviter_id == promo_user.id  # long-term unique lock wins


def test_agency_order_deducts_fund_but_pays_no_commission(db):
    agent_user, agent = make_agent(db, org_id="ORG-AGY", fund_fen=999900)
    promo_user, _ = make_promoter(db, code="TCH-AGENCY", org_id="ORG-AGY")
    customer = make_user(db)
    bind_referrer(db, user_id=customer.id, code="TCH-AGENCY")
    confirm_attribution(db, user_id=customer.id)

    product = db.query(Product).filter_by(sku=PERSONAL).one()
    order = Order(
        no="ORD-AGENCY-1",
        user_id=agent_user.id,
        product_id=product.id,
        sku=PERSONAL,
        amount_fen=69900,
        paid_amount_fen=69900,
        status="paid",
        channel="agent_fund",
        paid_at=utcnow(),
        idempotency_key="order:ORD-AGENCY-1",
    )
    db.add(order)
    db.flush()

    assert apply_order_commission(db, order=order) == []
    assert commission_for_order(db, order.no) == []
    fund_entry = (
        db.query(AgentFundEntry)
        .filter_by(ref_type="agency_order", ref_id=order.no)
        .one()
    )
    assert fund_entry.delta_fen == -41940  # 69900 * 60%
    assert fund_entry.balance_after_fen == 999900 - 41940
    assert agent.fund_fen == 999900 - 41940


def test_api_promote_summary_bind_and_withdraw_validation(db, client):
    promo_user, _ = make_promoter(db, code="TCH-API")
    customer = make_user(db)

    r = client.get("/api/promote/summary", headers=auth_headers(customer))
    assert r.status_code == 200, r.text
    assert r.json()["invite_code"] is None

    r = client.post(
        "/api/promote/bind", json={"code": "TCH-API"}, headers=auth_headers(customer)
    )
    assert r.status_code == 200, r.text
    assert r.json()["inviter_id"] == promo_user.id

    r = client.post(
        "/api/promote/confirm", headers=auth_headers(customer)
    )
    assert r.status_code == 200, r.text
    assert r.json()["confirmed"] is True

    r = client.post(
        "/api/promote/withdraw",
        json={"amount_fen": 1},
        headers=auth_headers(promo_user),
    )
    assert r.status_code == 400


# --- review-fix regressions --------------------------------------------------


def test_self_referral_199_no_invite_credits(db):
    """Important #1: own-code ¥199 purchases must not mint invite credits or eat the cap."""
    self_user = make_user(db, invite_code="TCH-SELF199")
    promoter = Promoter(user_id=self_user.id, level="basic", code="TCH-SELF199")
    db.add(promoter)
    db.flush()
    bind_referrer(db, user_id=self_user.id, code="TCH-SELF199")
    confirm_attribution(db, user_id=self_user.id)

    for n in range(3):
        paid_order(db, self_user, NEWCOMER, event_id=f"e{n}")

    assert (
        db.query(CreditEntry)
        .filter_by(user_id=self_user.id, ref_type="invite_bonus")
        .count()
        == 0
    )
    assert db.query(Promoter).filter_by(user_id=self_user.id).one().invited_count == 0
    assert db.query(CommissionEntry).filter_by(user_id=self_user.id).count() == 0


def test_expired_candidate_no_commission(db):
    """Important #2: candidate_at is the 90-day clock; confirmation never resets it."""
    promo_user, _ = make_promoter(db, code="TCH-EXPIRED", level="basic")
    customer = make_user(db)
    attribution = bind_referrer(db, user_id=customer.id, code="TCH-EXPIRED")
    attribution.candidate_at = utcnow() - timedelta(days=91)
    db.flush()
    confirm_attribution(db, user_id=customer.id)
    stored = db.query(Attribution).filter_by(user_id=customer.id).one()
    assert stored.confirmed_at is not None  # confirmed, but stale

    order = paid_order(db, customer, PERSONAL)
    assert db.query(Attribution).filter_by(user_id=customer.id).one().locked_at is None
    assert commission_for_order(db, order.no) == []


def test_agent_fund_never_negative(db):
    """Important #3: a 代开 order with too little fund must be rejected, not go negative."""
    agent_user, agent = make_agent(db, fund_fen=20000)  # 200 < 419.40 needed
    product = db.query(Product).filter_by(sku=PERSONAL).one()
    order = Order(
        no="ORD-AGENCY-POOR",
        user_id=agent_user.id,
        product_id=product.id,
        sku=PERSONAL,
        amount_fen=69900,
        paid_amount_fen=69900,
        status="paid",
        channel="agent_fund",
        paid_at=utcnow(),
        idempotency_key="order:ORD-AGENCY-POOR",
    )
    db.add(order)
    db.flush()

    with pytest.raises(InsufficientAgentFund):
        apply_order_commission(db, order=order)

    rows = db.query(AgentFundEntry).filter_by(agent_id=agent.id).all()
    assert len(rows) == 1  # only the opening balance row
    assert rows[0].delta_fen == 20000
    assert agent.fund_fen == 20000
    assert commission_for_order(db, order.no) == []


def test_locked_attribution_repurchase_after_90_days_pays_commission(db):
    """Round-2 fix: 90 days is only the candidate window; a lock is long-term."""
    promo_user, promo = make_promoter(db, code="TCH-LOCKED", level="basic")
    customer = make_user(db)
    bind_referrer(db, user_id=customer.id, code="TCH-LOCKED")
    confirm_attribution(db, user_id=customer.id)
    paid_order(db, customer, PERSONAL)  # first valid order locks the attribution

    locked = db.query(Attribution).filter_by(user_id=customer.id).one()
    assert locked.locked_at is not None
    locked_at = locked.locked_at
    # candidate_at is frozen at lock time; simulate 90+ days elapsing.
    locked.candidate_at = utcnow() - timedelta(days=91)
    db.flush()

    repurchase = paid_order(db, customer, PERSONAL, event_id="e2")
    entries = commission_for_order(db, repurchase.no)
    assert len(entries) == 1  # the repurchase price is 55920
    assert entries[0].promoter_id == promo.id
    assert entries[0].amount_fen == 55920 * 10 // 100  # basic repurchase 10%
    assert (
        db.query(Attribution).filter_by(user_id=customer.id).one().locked_at == locked_at
    )

    # Advanced promoter keeps its 15% repurchase rate on a long-term lock too.
    _, advanced = make_promoter(db, code="TCH-LOCKED-ADV", level="advanced")
    buyer = make_user(db)
    bind_referrer(db, user_id=buyer.id, code="TCH-LOCKED-ADV")
    confirm_attribution(db, user_id=buyer.id)
    paid_order(db, buyer, PERSONAL, event_id="e1")
    attr = db.query(Attribution).filter_by(user_id=buyer.id).one()
    attr.candidate_at = utcnow() - timedelta(days=91)
    db.flush()
    repurchase2 = paid_order(db, buyer, PERSONAL, event_id="e2")
    adv_entry = next(
        e for e in commission_for_order(db, repurchase2.no) if e.promoter_id == advanced.id
    )
    assert adv_entry.amount_fen == 55920 * 15 // 100
