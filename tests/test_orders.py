"""Order state machine, service operations and pay endpoints (Task 6).

The three tests named in the plan are kept verbatim by name; their bodies use
the shared in-memory fixture so user id 1 always exists.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.payment import mock as mock_adapter
from app.config import settings
from app.db import Base, get_db
from app.domain.order import (
    CLOSED,
    CREATED,
    FULFILLED,
    PAID,
    PENDING_PAYMENT,
    REFUNDED,
    REFUNDING,
    InvalidOrderTransition,
    assert_transition,
    can_transition,
)
from app.main import app
from app.models import AuditLog, CreditEntry, Order, OrderEvent, Product, Promoter, User
from app.services.auth import issue_token
from app.services.catalog import ProductNotFound
from app.services.credits import get_balance
from app.services.orders import (
    InvalidChannel,
    InvalidOrderState,
    OrderNotFound,
    PaymentAmountMismatch,
    begin_refund,
    cancel_order,
    confirm_payment,
    create_order,
    finish_refund,
    start_payment,
)
from scripts.seed import PRODUCTS

PERSONAL = "pkg_personal_699"
TEAM = "pkg_team_1999"
PENDING_RECOVERY = "pending_order_payment_in_progress"


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
    # The plan's assertions use user_id=1 directly, so seed that user first.
    session.add(
        User(phone="13800000001", display_name="U1", role="user", is_active=True)
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
            yield c
    finally:
        app.dependency_overrides.clear()


def auth_headers(db):
    user = db.get(User, 1)
    return {"Authorization": f"Bearer {issue_token(user, channel='web')}"}


def make_user(db, **kwargs):
    user = User(is_active=True, **kwargs)
    db.add(user)
    db.flush()
    return user


# --- the brief's three named tests -------------------------------------------


def test_order_state_machine(db):
    o = create_order(db, user_id=1, sku="pkg_personal_699", channel="native")
    assert o.status == "pending_payment"
    confirm_payment(db, order_no=o.no, event_id="e1", paid_amount_fen=69900)
    assert o.status == "fulfilled" and get_balance(db, 1) == 6000


def test_pending_order_recovery(db):
    o1 = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    o2 = create_order(db, user_id=1, sku=PERSONAL, channel="jsapi")
    assert o2.id == o1.id
    assert o2.no == o1.no
    assert o2.recovery == PENDING_RECOVERY

    # A different SKU, or a different user, must get its own pending order.
    other_sku = create_order(db, user_id=1, sku=TEAM, channel="native")
    assert other_sku.id != o1.id
    other_user = make_user(db, phone="13800000002")
    other_user_order = create_order(db, user_id=other_user.id, sku=PERSONAL, channel="native")
    assert other_user_order.id != o1.id
    assert db.query(Order).count() == 3


def test_duplicate_callback_is_idempotent(db):
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")

    def ledger_count_for_order(no):
        return db.query(CreditEntry).filter_by(ref_type="order", ref_id=no).count()

    confirm_payment(db, order_no=o.no, event_id="e1", paid_amount_fen=69900)
    confirm_payment(db, order_no=o.no, event_id="e1", paid_amount_fen=69900)
    assert ledger_count_for_order(o.no) == 1
    assert db.query(OrderEvent).filter_by(order_no=o.no, event_id="e1").count() == 1
    assert get_balance(db, 1) == 6000


# --- state machine -----------------------------------------------------------


def test_state_machine_transition_table(db):
    assert can_transition(CREATED, PENDING_PAYMENT)
    assert can_transition(PENDING_PAYMENT, PAID)
    assert can_transition(PAID, FULFILLED)
    assert can_transition(FULFILLED, REFUNDING)
    assert can_transition(REFUNDING, REFUNDED)
    assert not can_transition(CLOSED, PAID)
    assert not can_transition(REFUNDED, FULFILLED)
    with pytest.raises(InvalidOrderTransition):
        assert_transition(CLOSED, PAID)


# --- order creation / pricing ------------------------------------------------


def test_first_order_uses_server_list_price(db):
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    assert o.amount_fen == 69900
    assert o.idempotency_key == f"order:{o.no}"


def test_order_uses_repurchase_price_for_prior_paid_order(db):
    db.add(
        Order(
            no="ORD-HISTORICAL",
            user_id=1,
            sku=PERSONAL,
            amount_fen=69900,
            paid_amount_fen=69900,
            status=FULFILLED,
        )
    )
    db.commit()
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    assert o.amount_fen == 55920


def test_create_order_unknown_sku_raises(db):
    with pytest.raises(ProductNotFound):
        create_order(db, user_id=1, sku="missing", channel="native")


def test_create_order_rejects_unknown_channel(db):
    with pytest.raises(InvalidChannel):
        create_order(db, user_id=1, sku=PERSONAL, channel="alipay")


# --- payment / confirmation --------------------------------------------------


def test_start_payment_returns_gateway_payload(db):
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    payload = start_payment(db, o.no)
    assert payload["order_no"] == o.no
    assert payload["channel"] == "native"
    assert "code_url" in payload


def test_start_payment_unknown_order_raises(db):
    with pytest.raises(OrderNotFound):
        start_payment(db, "ORD-NOPE")


def test_confirm_grants_credits_bonuses_and_calls_commission_stub(db):
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    confirm_payment(db, order_no=o.no, event_id="e1", paid_amount_fen=69900)

    entry = db.query(CreditEntry).filter_by(ref_type="order", ref_id=o.no).one()
    assert entry.delta == 6000
    assert entry.entry_type == "purchase"
    assert entry.idempotency_key == f"purchase:{o.no}"
    assert db.query(Promoter).filter_by(user_id=1).one().level == "basic"
    # The affiliate stub is a no-op: Task 8 replaces it in place.
    from app.models import CommissionEntry

    assert db.query(CommissionEntry).count() == 0


def test_confirm_writes_order_event_with_deterministic_key(db):
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    confirm_payment(db, order_no=o.no, event_id="evt-42", paid_amount_fen=69900)
    event = db.query(OrderEvent).filter_by(order_no=o.no, event_id="evt-42").one()
    assert event.event_type == "payment_confirmed"
    assert event.payload["paid_amount_fen"] == 69900
    assert event.idempotency_key.startswith(f"event:{o.no}:")
    assert o.paid_at is not None and o.fulfilled_at is not None
    assert o.paid_amount_fen == 69900


def test_different_event_id_still_grants_credits_only_once(db):
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    confirm_payment(db, order_no=o.no, event_id="e1", paid_amount_fen=69900)
    confirm_payment(db, order_no=o.no, event_id="e2", paid_amount_fen=69900)
    assert get_balance(db, 1) == 6000
    assert db.query(CreditEntry).filter_by(ref_type="order", ref_id=o.no).count() == 1


def test_confirm_rejects_non_integer_amount(db):
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    with pytest.raises(ValueError):
        confirm_payment(db, order_no=o.no, event_id="e1", paid_amount_fen=69900.5)


def test_retry_creates_exactly_one_fund_row(db):
    """Deterministic key: a retried purchase writes one ledger row."""
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    for _ in range(3):
        confirm_payment(db, order_no=o.no, event_id="retry-e", paid_amount_fen=69900)
    assert db.query(CreditEntry).filter_by(idempotency_key=f"purchase:{o.no}").count() == 1


# --- cancel / refund ---------------------------------------------------------


def test_cancel_closed_order_is_idempotent(db):
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    first = cancel_order(db, o.no)
    second = cancel_order(db, o.no)
    assert first.status == CLOSED and second.id == first.id
    assert first.closed_at is not None
    assert db.query(OrderEvent).filter_by(order_no=o.no, event_type="order_cancelled").count() == 1


def test_cancel_paid_order_raises(db):
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    confirm_payment(db, order_no=o.no, event_id="e1", paid_amount_fen=69900)
    with pytest.raises(InvalidOrderState):
        cancel_order(db, o.no)


def test_refund_state_transitions(db):
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    confirm_payment(db, order_no=o.no, event_id="e1", paid_amount_fen=69900)
    refunding = begin_refund(db, o.no)
    assert refunding.status == REFUNDING
    refunded = finish_refund(db, o.no)
    assert refunded.status == REFUNDED


# --- API ---------------------------------------------------------------------


def test_api_create_and_native_pay(db, client):
    r = client.post(
        "/api/pay/orders",
        json={"sku": PERSONAL, "channel": "native"},
        headers=auth_headers(db),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == PENDING_PAYMENT
    order_no = body["order_no"]

    r2 = client.post(
        "/api/pay/native_pay",
        json={"order_no": order_no},
        headers=auth_headers(db),
    )
    assert r2.status_code == 200, r2.text
    assert "code_url" in r2.json()


def test_api_jsapi_pay(db, client):
    r = client.post(
        "/api/pay/orders",
        json={"sku": PERSONAL, "channel": "jsapi"},
        headers=auth_headers(db),
    )
    assert r.status_code == 200, r.text
    r2 = client.post(
        "/api/pay/jsapi_pay",
        json={"order_no": r.json()["order_no"]},
        headers=auth_headers(db),
    )
    assert r2.status_code == 200, r2.text
    assert "appId" in r2.json() and "package" in r2.json()


def test_api_pay_endpoint_rejects_wrong_channel(db, client):
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    r = client.post(
        "/api/pay/jsapi_pay",
        json={"order_no": o.no},
        headers=auth_headers(db),
    )
    assert r.status_code == 400


def test_api_cancel_order(db, client):
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    r = client.post(
        "/api/pay/cancel",
        json={"order_no": o.no},
        headers=auth_headers(db),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == CLOSED


def test_api_order_status(db, client):
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    assert client.get(f"/api/pay/orders/{o.no}").status_code == 401
    r = client.get(f"/api/pay/orders/{o.no}", headers=auth_headers(db))
    assert r.status_code == 200, r.text
    assert r.json()["order_no"] == o.no
    assert r.json()["gateway_status"] in (PENDING_PAYMENT, PAID)


# --- review-fix regressions --------------------------------------------------


def test_refund_reverses_commission_and_writes_refund_entry(db, monkeypatch):
    """Review Important #1: refund reverses commission, revokes entitlements,
    writes a refund ledger row, and stays idempotent."""
    commission_calls = []

    def commission_spy(db_, *, order):
        commission_calls.append(order.no)
        return []

    monkeypatch.setattr(
        "app.services.orders.reverse_order_commission", commission_spy
    )

    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    confirm_payment(db, order_no=o.no, event_id="e1", paid_amount_fen=69900)
    begin_refund(db, o.no)
    refunded = finish_refund(db, o.no)

    assert refunded.status == REFUNDED
    assert commission_calls == [o.no]

    refund_entry = (
        db.query(CreditEntry).filter_by(idempotency_key=f"refund:{o.no}").one()
    )
    assert refund_entry.entry_type == "refund"
    assert refund_entry.reason == "refund_fen:69900"

    revoke = db.query(AuditLog).filter_by(action="bonus:revoke", target_id="1").one()
    assert revoke.after["sku"] == PERSONAL
    assert revoke.after["order_no"] == o.no
    assert revoke.after["revoked"] is True

    # A repeated finish must not reverse twice or duplicate any marker row.
    finish_refund(db, o.no)
    assert commission_calls == [o.no]
    assert db.query(AuditLog).filter_by(action="bonus:revoke").count() == 1
    assert (
        db.query(CreditEntry).filter_by(idempotency_key=f"refund:{o.no}").count() == 1
    )


def test_paid_amount_mismatch_does_not_fulfill(db, client):
    """Review Important #2: paid amount must strictly equal the server price."""
    o = create_order(db, user_id=1, sku=PERSONAL, channel="native")

    with pytest.raises(PaymentAmountMismatch):
        confirm_payment(db, order_no=o.no, event_id="e1", paid_amount_fen=69900 - 1)
    assert db.query(Order).filter_by(no=o.no).one().status == PENDING_PAYMENT
    assert get_balance(db, 1) == 0
    assert (
        db.query(OrderEvent)
        .filter_by(order_no=o.no, event_type="payment_confirmed")
        .count()
        == 0
    )

    payload = {
        "order_no": o.no,
        "event_id": "e2",
        "paid_amount_fen": 69900 - 1,
        "timestamp": "1",
        "nonce": "n",
    }
    payload["signature"] = mock_adapter.sign(payload)
    r = client.post("/api/pay/sync", json=payload)
    assert r.status_code == 400, r.text
    assert db.query(Order).filter_by(no=o.no).one().status == PENDING_PAYMENT
    assert get_balance(db, 1) == 0

    # Floats are rejected at the boundary rather than coerced into fen.
    float_payload = {**payload, "event_id": "e3", "paid_amount_fen": 69900.0}
    float_payload["signature"] = mock_adapter.sign(float_payload)
    assert client.post("/api/pay/sync", json=float_payload).status_code == 400
    assert get_balance(db, 1) == 0


# --- final review regressions -------------------------------------------------


def test_purchase_credits_get_expiry_and_renewal(db, monkeypatch):
    """Final review Important #2: purchase lots expire and renew on recharge.

    The first order's purchase lot expires ~CREDIT_EXPIRE_DAYS out. A second
    recharge extends that still-active lot under RENEW_MODE=all_active; under
    ``new_only`` it is left untouched.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    first = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    confirm_payment(db, order_no=first.no, event_id="e1", paid_amount_fen=69900)
    lot1 = db.query(CreditEntry).filter_by(idempotency_key=f"purchase:{first.no}").one()
    assert lot1.expires_at is not None
    assert settings.credit_expire_days - 1 <= (lot1.expires_at - now).days \
        <= settings.credit_expire_days

    # Make the first expiry observably older so renewal is visible.
    lot1.expires_at = lot1.expires_at - timedelta(days=100)
    db.commit()

    second = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    assert second.no != first.no
    confirm_payment(
        db, order_no=second.no, event_id="e2", paid_amount_fen=second.amount_fen
    )
    lot2 = db.query(CreditEntry).filter_by(idempotency_key=f"purchase:{second.no}").one()
    db.refresh(lot1)
    assert lot1.expires_at == lot2.expires_at  # all_active extended lot 1 too

    # new_only leaves earlier lots untouched.
    monkeypatch.setattr(settings, "renew_mode", "new_only")
    lot1.expires_at = lot1.expires_at - timedelta(days=50)
    lot2.expires_at = lot2.expires_at - timedelta(days=50)
    older1, older2 = lot1.expires_at, lot2.expires_at
    db.commit()

    third = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    confirm_payment(
        db, order_no=third.no, event_id="e3", paid_amount_fen=third.amount_fen
    )
    lot3 = db.query(CreditEntry).filter_by(idempotency_key=f"purchase:{third.no}").one()
    db.refresh(lot1)
    db.refresh(lot2)
    assert lot1.expires_at == older1 and lot2.expires_at == older2
    assert lot3.expires_at > older2


def test_refund_records_initiated_marker_before_gateway(db, monkeypatch):
    """Final review Important #4: a durable marker precedes the gateway call."""
    calls = []

    class SpyGateway:
        def refund(self, order_no, amount_fen):
            # By the time the gateway is called the marker must be committed.
            marker = (
                db.query(OrderEvent)
                .filter_by(order_no=order_no, event_type="refund_initiated")
                .first()
            )
            calls.append((order_no, amount_fen, marker is not None))
            return True

    monkeypatch.setattr("app.services.orders.get_gateway", lambda: SpyGateway())
    order = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    confirm_payment(db, order_no=order.no, event_id="e1", paid_amount_fen=69900)
    begin_refund(db, order.no)

    finish_refund(db, order.no, amount_fen=69900)

    assert calls == [(order.no, 69900, True)]
    marker = (
        db.query(OrderEvent)
        .filter_by(order_no=order.no, event_type="refund_initiated")
        .one()
    )
    assert marker.payload["amount_fen"] == 69900
    assert marker.idempotency_key == f"event:{order.no}:refund_initiated:na"
