"""Payment adapter contracts, signature verification and webhook idempotency."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.payment import InvalidSignature, PaymentGateway, get_gateway
from app.adapters.payment import mock as mock_adapter
from app.adapters.payment import wechat as wechat_adapter
from app.db import Base, get_db
from app.main import app
from app.models import Order, OrderEvent, Product, User
from app.services.credits import get_balance
from app.services.orders import create_order
from scripts.seed import PRODUCTS

PERSONAL = "pkg_personal_699"


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


def _callback_payload(order_no, event_id="evt-1", amount=69900):
    return {
        "order_no": order_no,
        "event_id": event_id,
        "paid_amount_fen": amount,
        "timestamp": "1700000000",
        "nonce": "nonce-abc",
    }


# --- adapters satisfy the pinned protocol ------------------------------------


def test_gateways_satisfy_payment_gateway_protocol():
    assert isinstance(mock_adapter.MockGateway(), PaymentGateway)
    assert isinstance(wechat_adapter.WeChatGateway(), PaymentGateway)
    assert isinstance(get_gateway(), PaymentGateway)


def test_mock_signature_roundtrip():
    gateway = mock_adapter.MockGateway()
    payload = _callback_payload("ORD-1")
    signature = mock_adapter.sign(payload)
    assert gateway.verify_callback(payload, signature) == "ORD-1"
    with pytest.raises(InvalidSignature):
        gateway.verify_callback(payload, "deadbeef")
    tampered = dict(payload, paid_amount_fen=1)
    with pytest.raises(InvalidSignature):
        gateway.verify_callback(tampered, signature)


def test_wechat_signature_roundtrip():
    gateway = wechat_adapter.WeChatGateway()
    payload = _callback_payload("ORD-2")
    signature = wechat_adapter.sign(payload)
    assert gateway.verify_callback(payload, signature) == "ORD-2"
    with pytest.raises(InvalidSignature):
        gateway.verify_callback(payload, "bad")
    with pytest.raises(InvalidSignature):
        gateway.verify_callback(dict(payload, nonce="other"), signature)


# --- adapter operations ------------------------------------------------------


def test_mock_gateway_create_query_refund(db):
    order = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    payload = mock_adapter.MockGateway().create_payment(order)
    assert payload["code_url"].startswith("mock://")

    gateway = mock_adapter.MockGateway()
    assert gateway.query(order.no) == "pending_payment"
    gateway.mark_paid(order.no)
    assert gateway.query(order.no) == "paid"
    assert gateway.refund(order.no, 100) is True


def test_wechat_gateway_native_and_jsapi_orders(db):
    native = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    jsapi = create_order(db, user_id=1, sku="pkg_team_1999", channel="jsapi")
    gateway = wechat_adapter.WeChatGateway()

    native_payload = gateway.create_payment(native)
    assert native_payload["code_url"].startswith("weixin://")
    jsapi_payload = gateway.create_payment(jsapi)
    assert jsapi_payload["appId"]
    assert jsapi_payload["package"].startswith("prepay_id=")
    assert jsapi_payload["signType"] == "RSA"
    assert jsapi_payload["paySign"]

    assert gateway.query(native.no) == "pending_payment"
    assert gateway.refund(native.no, 100) is True


def test_create_payment_rejects_unknown_channel(db):
    order = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    order.channel = "alipay"
    with pytest.raises(ValueError):
        mock_adapter.MockGateway().create_payment(order)


# --- webhook / sync endpoint -------------------------------------------------


def test_sync_endpoint_verifies_signature(db, client):
    order = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    bad = dict(_callback_payload(order.no), signature="deadbeef")
    r = client.post("/api/pay/sync", json=bad)
    assert r.status_code == 400, r.text
    assert get_balance(db, 1) == 0
    assert db.query(OrderEvent).filter_by(order_no=order.no).count() == 1


def test_sync_endpoint_confirms_and_is_idempotent(db, client):
    order = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    payload = _callback_payload(order.no, event_id="webhook-1")
    payload["signature"] = mock_adapter.sign(payload)

    first = client.post("/api/pay/sync", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "fulfilled"
    assert get_balance(db, 1) == 6000

    second = client.post("/api/pay/sync", json=payload)
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "fulfilled"
    assert get_balance(db, 1) == 6000
    assert db.query(Order).filter_by(no=order.no).one().status == "fulfilled"
    assert db.query(OrderEvent).filter_by(order_no=order.no).count() == 2


def test_sync_unknown_order_is_404(db, client):
    payload = _callback_payload("ORD-MISSING")
    payload["signature"] = mock_adapter.sign(payload)
    r = client.post("/api/pay/sync", json=payload)
    assert r.status_code == 404


def test_sync_rejects_callback_for_closed_order(db, client):
    order = create_order(db, user_id=1, sku=PERSONAL, channel="native")
    from app.services.orders import cancel_order

    cancel_order(db, order.no)
    payload = _callback_payload(order.no)
    payload["signature"] = mock_adapter.sign(payload)
    r = client.post("/api/pay/sync", json=payload)
    assert r.status_code == 409
    assert get_balance(db, 1) == 0
