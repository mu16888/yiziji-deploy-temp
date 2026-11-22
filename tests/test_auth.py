"""Tests for identity, roles and dual token channels (Task 3)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.db import Base, get_db
from app.domain.identity import role_satisfies
from app.main import app
from app.middleware.csrf import CSRFMiddleware
from app.models import User


@pytest.fixture()
def client():
    """Isolated in-memory DB per test; auth endpoints stay real."""
    # StaticPool shares one in-memory connection across TestClient's worker
    # thread and the fixture's main thread.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_register_and_login_with_dev_code(client):
    """The two assertions from the brief, verbatim."""
    r = client.post(
        "/api/auth/register",
        json={"phone": "13800000001", "code": "123456", "referrer": "TCH-8F3K"},
    )
    assert r.status_code == 200
    assert "token" in r.json()
    assert client.get("/api/credits/balance").status_code == 200


def test_web_cookie_and_bearer_both_work(client):
    """Cookie channel and Authorization: Bearer channel both resolve a user."""
    r = client.post(
        "/api/auth/register",
        json={"phone": "13800000002", "code": "123456"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    # Cookie channel: this TestClient kept the httpOnly cookie from register.
    r_cookie = client.get("/api/credits/balance")
    assert r_cookie.status_code == 200
    assert r_cookie.json()["balance"] == 100

    # Bearer channel: a fresh client with no cookies at all.
    with TestClient(app) as fresh:
        r_bearer = fresh.get(
            "/api/credits/balance",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r_bearer.status_code == 200, r_bearer.text
        assert r_bearer.json()["balance"] == 100


def test_register_grants_100_credits_once(client):
    payload = {"phone": "13800000003", "code": "123456"}
    assert client.post("/api/auth/register", json=payload).status_code == 200
    # Re-registering the same phone must not double-grant the signup credits.
    assert client.post("/api/auth/register", json=payload).status_code == 200
    assert client.get("/api/credits/balance").json()["balance"] == 100


def test_role_hierarchy_is_admin_ge_agent_ge_promoter_ge_user():
    """Ruling: admin >= agent >= promoter >= user (NOT agent >= admin)."""
    for required in ("admin", "agent", "promoter", "user"):
        assert role_satisfies("admin", required)
    assert role_satisfies("agent", "agent")
    assert role_satisfies("agent", "promoter")
    assert role_satisfies("agent", "user")
    assert role_satisfies("promoter", "promoter")
    assert role_satisfies("promoter", "user")
    assert role_satisfies("user", "user")
    assert not role_satisfies("user", "promoter")
    assert not role_satisfies("user", "agent")
    assert not role_satisfies("user", "admin")
    assert not role_satisfies("promoter", "agent")
    assert not role_satisfies("agent", "admin")


def test_token_payload_carries_claims_and_rejects_tampering():
    from app.services.auth import decode_token, issue_token

    user = User(id=7, role="user")
    token = issue_token(user, channel="api", referrer_code="TCH-8F3K")
    payload = decode_token(token)
    assert payload["sub"] == "7"
    assert payload["channel"] == "api"
    assert payload["referrer_code"] == "TCH-8F3K"
    assert decode_token(token + "x") is None


def test_require_roles_guard_uses_hierarchy():
    from fastapi import HTTPException

    from app.services.auth import require_roles

    guard = require_roles("promoter")
    assert guard(User(role="agent", is_active=True)).role == "agent"
    assert guard(User(role="admin", is_active=True)).role == "admin"
    with pytest.raises(HTTPException) as exc:
        guard(User(role="user", is_active=True))
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        require_roles("agent")(User(role="promoter", is_active=True))
    assert exc.value.status_code == 403


def test_invalid_dev_code_rejected(client):
    r = client.post(
        "/api/auth/register",
        json={"phone": "13800000009", "code": "000000"},
    )
    assert r.status_code == 401


def test_unauthenticated_balance_is_401(client):
    with TestClient(app) as fresh:
        assert fresh.get("/api/credits/balance").status_code == 401


def test_login_by_phone_and_dev_code(client):
    reg = client.post(
        "/api/auth/register",
        json={"phone": "13800000004", "code": "123456"},
    )
    assert reg.status_code == 200, reg.text
    with TestClient(app) as fresh:
        r = fresh.post(
            "/api/auth/login",
            json={"provider": "password", "phone": "13800000004", "credential": "123456"},
        )
        assert r.status_code == 200, r.text
        assert "token" in r.json()
        assert fresh.get("/api/credits/balance").status_code == 200


def test_csrf_double_submit_and_bearer_bypass():
    """CSRF protects cookie writes; Bearer writes are exempt."""

    def ok(request):
        return JSONResponse({"ok": True})

    mini = CSRFMiddleware(Starlette(routes=[Route("/x", ok, methods=["POST"])]))
    c = TestClient(mini)
    c.cookies.set("access_token", "t")
    c.cookies.set("csrf_token", "abc")

    # Cookie-authenticated write without a CSRF header is rejected.
    assert c.post("/x").status_code == 403

    # Matching double-submit header is accepted.
    assert c.post("/x", headers={"X-CSRF-Token": "abc"}).status_code == 200

    # Bearer-authenticated write bypasses CSRF.
    assert c.post("/x", headers={"Authorization": "Bearer t"}).status_code == 200
