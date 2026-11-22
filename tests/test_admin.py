"""Admin console: RBAC, audited writes, refund routing and stats (Task 9).

The two tests named in the brief are kept verbatim by name. ``client`` is an
authenticated *non-admin* client so the brief's bare 403 assertion holds;
admin calls pass an explicit Bearer header (which also bypasses CSRF).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.payment import mock as mock_adapter
from app.db import Base, get_db
from app.main import app
from app.models import (
    Agent,
    AgentFundEntry,
    AuditLog,
    CommissionEntry,
    CreditEntry,
    Order,
    Product,
    Promoter,
    User,
    Withdrawal,
)
from app.services.affiliate import bind_referrer, confirm_attribution
from app.services.auth import issue_token
from app.services.credits import get_balance
from app.services.orders import confirm_payment, create_order
from scripts.seed import PRODUCTS

PERSONAL = "pkg_personal_699"


# --- fixtures ----------------------------------------------------------------


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
    session.flush()
    session.add_all(
        [
            User(phone="13900000003", display_name="admin", role="admin", is_active=True),
            User(phone="13800000001", display_name="user", role="user", is_active=True),
        ]
    )
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
            # Default identity is a normal user -> /admin/* must answer 403.
            normal = db.query(User).filter_by(role="user").one()
            c.headers.update({"Authorization": f"Bearer {issue_token(normal)}"})
            yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def admin(db):
    return db.query(User).filter_by(role="admin").one()


@pytest.fixture()
def normal(db):
    return db.query(User).filter_by(role="user").one()


@pytest.fixture()
def admin_headers(admin):
    return {"Authorization": f"Bearer {issue_token(admin)}"}


# --- RBAC --------------------------------------------------------------------


def test_non_admin_forbidden(client):
    assert client.get("/admin/orders").status_code == 403


def test_unauthenticated_admin_is_401(db):
    with TestClient(app) as fresh:
        assert fresh.get("/admin/orders").status_code == 401


# --- credit adjustment -------------------------------------------------------


def test_manual_credit_adjustment_requires_reason_and_is_audited(
    db, client, normal, admin, admin_headers
):
    # Empty reason is rejected before any money or audit row is written.
    r = client.post(
        "/admin/credits/adjust",
        json={"user_id": normal.id, "delta": 50, "reason": ""},
        headers=admin_headers,
    )
    assert r.status_code == 422
    assert db.query(CreditEntry).count() == 0
    assert db.query(AuditLog).count() == 0

    # A valid adjustment credits the user and leaves a complete audit record.
    r = client.post(
        "/admin/credits/adjust",
        json={
            "user_id": normal.id,
            "delta": 50,
            "reason": "补发活动积分",
            "idempotency_key": "admin-adj-1",
        },
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert get_balance(db, normal.id) == 50
    audit = db.query(AuditLog).filter_by(action="admin:credit_adjust").one()
    assert audit.actor_id == admin.id
    assert audit.after["delta"] == 50
    assert audit.reason == "补发活动积分"
    assert audit.target_id == str(normal.id)
    assert audit.created_at is not None


def test_credit_adjustment_is_idempotent(db, client, normal, admin_headers):
    payload = {
        "user_id": normal.id,
        "delta": 30,
        "reason": "人工补偿",
        "idempotency_key": "admin-adj-2",
    }
    assert client.post("/admin/credits/adjust", json=payload, headers=admin_headers).status_code == 200
    assert client.post("/admin/credits/adjust", json=payload, headers=admin_headers).status_code == 200
    assert get_balance(db, normal.id) == 30
    assert db.query(CreditEntry).filter_by(idempotency_key="admin-adj-2").count() == 1
    assert db.query(AuditLog).filter_by(action="admin:credit_adjust").count() == 1


# --- product config ----------------------------------------------------------


def test_product_config_update_is_audited(db, client, admin, admin_headers):
    product = db.query(Product).filter_by(sku=PERSONAL).one()
    before = product.amount_fen
    r = client.patch(
        f"/admin/products/{PERSONAL}",
        json={"amount_fen": 59900, "reason": "促销调价"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    db.refresh(product)
    assert product.amount_fen == 59900
    audit = db.query(AuditLog).filter_by(action="admin:product_update").one()
    assert audit.actor_id == admin.id
    assert audit.before["amount_fen"] == before
    assert audit.after["amount_fen"] == 59900


# --- orders ------------------------------------------------------------------


def test_admin_can_list_orders(db, client, normal, admin_headers):
    order = create_order(db, user_id=normal.id, sku=PERSONAL, channel="native")
    r = client.get("/admin/orders", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert any(row["no"] == order.no for row in r.json())


def test_order_manual_sync_confirms_paid_order(db, client, normal, admin_headers):
    order = create_order(db, user_id=normal.id, sku=PERSONAL, channel="native")
    mock_adapter.MockGateway().mark_paid(order.no)
    r = client.post(f"/admin/orders/{order.no}/sync", json={}, headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "fulfilled"
    assert get_balance(db, normal.id) == 6000
    assert db.query(AuditLog).filter_by(action="admin:order_sync").count() == 1


def test_refund_approval_routes_through_refund_service(db, client, normal, admin_headers):
    promoter_user = User(
        phone="13911112222", display_name="p", role="promoter", is_active=True,
        invite_code="TCH-P1",
    )
    db.add(promoter_user)
    db.flush()
    db.add(Promoter(user_id=promoter_user.id, level="basic", code="TCH-P1"))
    db.flush()
    bind_referrer(db, user_id=normal.id, code="TCH-P1")
    confirm_attribution(db, user_id=normal.id)
    db.commit()

    order = create_order(db, user_id=normal.id, sku=PERSONAL, channel="native")
    confirm_payment(db, order_no=order.no, event_id="e1", paid_amount_fen=69900)
    commissions = db.query(CommissionEntry).filter_by(order_no=order.no).all()
    assert commissions and all(c.status == "pending" for c in commissions)

    r = client.post(
        f"/admin/orders/{order.no}/refund",
        json={"reason": "用户退款申请"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    db.refresh(order)
    assert order.status == "refunded"
    assert all(
        c.status == "reversed"
        for c in db.query(CommissionEntry).filter_by(order_no=order.no).all()
    )
    assert db.query(CreditEntry).filter_by(idempotency_key=f"refund:{order.no}").count() == 1
    audit = db.query(AuditLog).filter_by(action="admin:refund_approve").one()
    assert audit.after["status"] == "refunded"
    assert audit.reason == "用户退款申请"


# --- agent review / fund -----------------------------------------------------


def test_agent_review_is_audited(db, client, admin, admin_headers):
    agent_user = User(phone="13933334444", display_name="a", role="user", is_active=True)
    db.add(agent_user)
    db.flush()
    agent = Agent(user_id=agent_user.id, status="pending")
    db.add(agent)
    db.commit()

    r = client.post(
        f"/admin/agents/{agent.id}/review",
        json={"approved": True, "reason": "资料齐全"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    db.refresh(agent)
    db.refresh(agent_user)
    assert agent.status == "active"
    assert agent_user.role == "agent"
    audit = db.query(AuditLog).filter_by(action="admin:agent_review").one()
    assert audit.actor_id == admin.id
    assert audit.after["status"] == "active"


def test_agent_fund_adjustment_is_audited_and_never_negative(db, client, admin_headers):
    agent_user = User(phone="13955556666", display_name="a2", role="agent", is_active=True)
    db.add(agent_user)
    db.flush()
    agent = Agent(user_id=agent_user.id, status="active", fund_fen=0)
    db.add(agent)
    db.commit()

    r = client.post(
        f"/admin/agents/{agent.id}/fund/adjust",
        json={"delta_fen": 5000, "reason": "人工清算补款", "idempotency_key": "fund-1"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    db.refresh(agent)
    assert agent.fund_fen == 5000
    assert db.query(AgentFundEntry).filter_by(idempotency_key="fund-1").count() == 1

    # A debit beyond the balance is rejected and never writes a negative row.
    r = client.post(
        f"/admin/agents/{agent.id}/fund/adjust",
        json={"delta_fen": -6000, "reason": "超额扣款", "idempotency_key": "fund-2"},
        headers=admin_headers,
    )
    assert r.status_code == 400
    db.refresh(agent)
    assert agent.fund_fen == 5000
    assert db.query(AuditLog).filter_by(action="admin:agent_fund_adjust").count() == 1


# --- commission / withdrawal review -----------------------------------------


def test_withdrawal_review_calls_service_and_is_audited(db, client, normal, admin_headers):
    withdrawal = Withdrawal(
        user_id=normal.id, amount_fen=10000, status="pending", idempotency_key="w-1"
    )
    db.add(withdrawal)
    db.commit()

    r = client.post(
        f"/admin/withdrawals/{withdrawal.id}/review",
        json={"approved": True, "reason": "审核通过"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    db.refresh(withdrawal)
    assert withdrawal.status == "approved"
    assert withdrawal.reviewed_by is None  # plan-mandated: reviewer arg deferred
    audit = db.query(AuditLog).filter_by(action="admin:withdrawal_review").one()
    assert audit.after["status"] == "approved"
    assert audit.reason == "审核通过"


# --- stats / audit -----------------------------------------------------------


def test_stats_dashboard(db, client, normal, admin_headers):
    from app.domain.ledger import reserve

    order = create_order(db, user_id=normal.id, sku=PERSONAL, channel="native")
    confirm_payment(db, order_no=order.no, event_id="e1", paid_amount_fen=69900)
    reserve(
        db, user_id=normal.id, amount=100,
        ref_type="job", ref_id="j1", idempotency_key="reserve:j1",
    )
    db.add(
        CommissionEntry(
            user_id=normal.id, amount_fen=5000, status="pending",
            idempotency_key="c-1", ref_type="order", ref_id=order.no,
        )
    )
    db.commit()

    r = client.get("/admin/stats", headers=admin_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["registered_users"] == 2
    assert data["paid_amount_fen"] == 69900
    assert data["credits_consumed"] == 100
    assert data["pending_commission_fen"] == 5000


def test_audit_log_endpoint_lists_writes(db, client, normal, admin_headers):
    client.post(
        "/admin/credits/adjust",
        json={"user_id": normal.id, "delta": 5, "reason": "x", "idempotency_key": "adj-audit"},
        headers=admin_headers,
    )
    r = client.get("/admin/audit-logs", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert any(row["action"] == "admin:credit_adjust" for row in r.json())


# --- HTML pages --------------------------------------------------------------


def test_admin_pages_are_protected_and_render(db, client, admin_headers):
    for path in ("/admin", "/admin/ui/orders", "/admin/ui/products", "/admin/ui/withdrawals"):
        assert client.get(path).status_code == 403
        r = client.get(path, headers=admin_headers)
        assert r.status_code == 200, (path, r.text[:200])
        assert "管理后台" in r.text


def test_admin_refund_cannot_exceed_paid(db, client, normal, admin_headers, monkeypatch):
    """Final review Important #4: clamp refund amount at the API boundary."""
    calls = []

    class SpyGateway:
        def refund(self, order_no, amount_fen):
            calls.append((order_no, amount_fen))
            return True

    monkeypatch.setattr("app.api.admin.get_gateway", lambda: SpyGateway())
    monkeypatch.setattr("app.services.orders.get_gateway", lambda: SpyGateway())

    order = create_order(db, user_id=normal.id, sku=PERSONAL, channel="native")
    confirm_payment(db, order_no=order.no, event_id="e1", paid_amount_fen=69900)

    r = client.post(
        f"/admin/orders/{order.no}/refund",
        json={"amount_fen": 69901, "reason": "over refund"},
        headers=admin_headers,
    )
    assert r.status_code == 400, r.text
    assert calls == []  # gateway never called
    db.refresh(order)
    assert order.status == "fulfilled"  # no begin_refund / status change
    assert db.query(AuditLog).filter_by(action="admin:refund_approve").count() == 0

    # A negative amount is rejected for the same reason.
    r = client.post(
        f"/admin/orders/{order.no}/refund",
        json={"amount_fen": -1, "reason": "negative"},
        headers=admin_headers,
    )
    assert r.status_code == 400
    assert calls == []
    db.refresh(order)
    assert order.status == "fulfilled"
