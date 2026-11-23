"""Tests for the product catalogue and server-side pricing (Task 5).

All prices asserted here come from ``scripts.seed.PRODUCTS`` / ``Product`` rows;
the client never supplies a price.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Agent, AuditLog, Order, Product, Promoter, User
from app.services.auth import issue_token
from app.services.catalog import (
    ProductNotFound,
    apply_bonuses,
    get_price,
    list_products,
)
from scripts.seed import PRODUCTS

NEWCOMER = "pkg_newcomer_experience_199"
PERSONAL = "pkg_personal_699"
ENTERPRISE = "pkg_enterprise_collab_3999"
AGENT_SKU = "agent_admission_9999"


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


def make_user(db, **kwargs):
    user = User(is_active=True, **kwargs)
    db.add(user)
    db.flush()
    return user


def make_user_with_order(
    db, *, sku, paid_fen=69900, status="fulfilled", refund_status=None, **user_kwargs
):
    """A user with one historical paid order for ``sku``."""
    user = make_user(db, **user_kwargs)
    db.add(
        Order(
            no=f"ORD-{sku}-{user.id}-{paid_fen}",
            user_id=user.id,
            sku=sku,
            amount_fen=paid_fen,
            paid_amount_fen=paid_fen,
            status=status,
            refund_status=refund_status,
        )
    )
    db.commit()
    return user


# --- server-side pricing (the brief's two assertions, verbatim) --------------


def test_repurchase_price_is_server_side(db):
    u = make_user_with_order(db, sku=PERSONAL, paid_fen=69900)
    q = get_price(db, PERSONAL, u)
    assert q.amount_fen == 55920 and q.original_amount_fen == 69900
    assert q.credits == 6000
    assert q.eligible is True


def test_199_no_cash_commission_but_locks_attribution(db):
    views = {p.sku: p for p in list_products(db, make_user(db))}
    newcomer = views[NEWCOMER]
    assert newcomer.cash_commission is False
    assert newcomer.locks_attribution is True
    # A normal package does earn cash commission and also locks attribution.
    assert views[PERSONAL].cash_commission is True
    assert views[PERSONAL].locks_attribution is True


# --- price rules -------------------------------------------------------------


def test_first_purchase_uses_list_price(db):
    q = get_price(db, PERSONAL, make_user(db))
    assert q.amount_fen == 69900 and q.original_amount_fen == 69900
    assert q.eligible is True


def test_refunded_order_does_not_grant_repurchase_price(db):
    u = make_user_with_order(db, sku=PERSONAL, paid_fen=69900, refund_status="refunded")
    assert get_price(db, PERSONAL, u).amount_fen == 69900


def test_pending_order_does_not_grant_repurchase_price(db):
    u = make_user_with_order(db, sku=PERSONAL, paid_fen=69900, status="pending_payment")
    assert get_price(db, PERSONAL, u).amount_fen == 69900


def test_repurchase_matches_unified_subject(db):
    make_user_with_order(
        db,
        sku=PERSONAL,
        paid_fen=69900,
        identity_hash="HASH-A",
        org_id="ORG-A",
    )
    same_identity = make_user(db, identity_hash="HASH-A")
    same_org = make_user(db, org_id="ORG-A")
    stranger = make_user(db, identity_hash="HASH-B", org_id="ORG-B")

    assert get_price(db, PERSONAL, same_identity).amount_fen == 55920
    assert get_price(db, PERSONAL, same_org).amount_fen == 55920
    assert get_price(db, PERSONAL, stranger).amount_fen == 69900


def test_repurchase_is_sku_specific(db):
    u = make_user_with_order(db, sku=PERSONAL, paid_fen=69900)
    # A different SKU's repurchase price must not leak onto this one.
    q = get_price(db, "pkg_team_1999", u)
    assert q.amount_fen == 199900 and q.original_amount_fen == 199900


def test_get_price_unknown_sku_raises(db):
    with pytest.raises(ProductNotFound):
        get_price(db, "no_such_sku", make_user(db))


# --- catalogue listing -------------------------------------------------------


def test_list_products_exposes_server_prices_and_repurchase_flag(db):
    u = make_user_with_order(db, sku=PERSONAL, paid_fen=69900)
    views = {p.sku: p for p in list_products(db, u)}
    v = views[PERSONAL]
    assert v.amount_fen == 55920
    assert v.original_amount_fen == 69900
    assert v.credits == 6000
    assert v.repurchase is True
    assert v.eligible is True
    assert v.amount_yuan == "559.20"


def test_list_products_hides_inactive_products(db):
    # The retired voice-clone SKU is no longer seeded, so use a live package to
    # keep this coverage meaningful: inactive products must never be listed.
    u = make_user(db)
    product = db.query(Product).filter_by(sku="pkg_starter_9").one()
    product.active = False
    db.commit()
    assert "pkg_starter_9" not in {p.sku for p in list_products(db, u)}


# --- apply_bonuses -----------------------------------------------------------


def test_apply_bonuses_promoter_level(db):
    u = make_user(db)
    apply_bonuses(db, u.id, PERSONAL)
    promoter = db.query(Promoter).filter_by(user_id=u.id).one()
    assert promoter.level == "basic"
    assert u.role == "promoter"


def test_apply_bonuses_agent_record(db):
    u = make_user(db)
    apply_bonuses(db, u.id, AGENT_SKU)
    agent = db.query(Agent).filter_by(user_id=u.id).one()
    assert agent.status == "pending"


def test_apply_bonuses_org_seats(db):
    u = make_user(db)
    apply_bonuses(db, u.id, ENTERPRISE)
    assert u.org_id
    log = db.query(AuditLog).filter_by(action="bonus:grant", target_id=str(u.id)).one()
    assert log.after["seats"] == 10
    assert log.after["org"] is True
    assert log.after["promoter_level"] == "advanced"


def test_apply_bonuses_records_course_and_voice_entitlements(db):
    db.add(
        Product(
            sku="bundle_course_voice",
            name="课程音色包",
            type="package",
            amount_fen=10000,
            credits=0,
            bonuses={"course": "ai-class-101", "voice": "teacher-timbre"},
            eligibility={},
            sort=9,
        )
    )
    db.commit()
    u = make_user(db)
    apply_bonuses(db, u.id, "bundle_course_voice")
    log = db.query(AuditLog).filter_by(action="bonus:grant", target_id=str(u.id)).one()
    assert log.after["course"] == "ai-class-101"
    assert log.after["voice"] == "teacher-timbre"


def test_apply_bonuses_does_not_downgrade_promoter_level(db):
    u = make_user(db)
    apply_bonuses(db, u.id, "pkg_team_1999")
    apply_bonuses(db, u.id, PERSONAL)
    promoter = db.query(Promoter).filter_by(user_id=u.id).one()
    assert promoter.level == "advanced"


def test_apply_bonuses_is_idempotent(db):
    u = make_user(db)
    apply_bonuses(db, u.id, PERSONAL)
    apply_bonuses(db, u.id, PERSONAL)
    assert db.query(Promoter).filter_by(user_id=u.id).count() == 1
    assert db.query(AuditLog).filter_by(action="bonus:grant", target_id=str(u.id)).count() == 1


def test_apply_bonuses_unknown_sku_is_noop(db):
    u = make_user(db)
    apply_bonuses(db, u.id, "missing_sku")
    assert db.query(AuditLog).count() == 0


# --- API + pricing page ------------------------------------------------------


def test_catalog_api_returns_server_computed_price(db, client):
    assert client.get("/api/catalog/products").status_code == 401
    u = make_user_with_order(db, sku=PERSONAL, paid_fen=69900)
    client.cookies.set("access_token", issue_token(u, channel="web"))
    r = client.get(f"/api/catalog/price/{PERSONAL}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["amount_fen"] == 55920
    assert body["credits"] == 6000
    assert body["original_amount_fen"] == 69900
    assert body["eligible"] is True

    listing = client.get("/api/catalog/products")
    assert listing.status_code == 200, listing.text
    assert any(
        p["sku"] == PERSONAL and p["amount_fen"] == 55920 for p in listing.json()
    )


def test_catalog_api_unknown_sku_is_404(db, client):
    u = make_user(db)
    client.cookies.set("access_token", issue_token(u, channel="web"))
    r = client.get("/api/catalog/price/no_such_sku")
    assert r.status_code == 404


def test_pricing_page_renders_only_server_prices(db, client):
    u = make_user_with_order(db, sku=PERSONAL, paid_fen=69900)
    client.cookies.set("access_token", issue_token(u, channel="web"))
    r = client.get("/pricing")
    assert r.status_code == 200, r.text
    assert "559.20" in r.text  # server-computed repurchase price
    assert "个人版" in r.text

    # The template must contain no hard-coded price literals.
    src = Path("templates/pricing.html").read_text(encoding="utf-8")
    for literal in ("19900", "69900", "199900", "399900", "29900", "999900", "55920", "¥699", "699.00"):
        assert literal not in src


def test_pricing_page_is_public(db, client):
    r = client.get("/pricing")
    assert r.status_code == 200, r.text
    assert "699.00" in r.text  # list price for anonymous visitors


# --- pricing/page adjustments ------------------------------------------------


def test_hidden_skus_not_listed(db):
    """Agent admission and the retired voice clone never appear for sale."""
    # Simulate the live DB, which may still carry the retired voice SKU even
    # though the seed no longer creates it. The filter must work either way.
    if db.query(Product).filter_by(sku="svc_voice_clone_299").first() is None:
        db.add(
            Product(
                sku="svc_voice_clone_299",
                name="专属音色复刻",
                type="service",
                amount_fen=29900,
                original_amount_fen=None,
                credits=0,
                bonuses={},
                eligibility={},
                sort=5,
                active=True,
            )
        )
        db.commit()

    listed = {p.sku for p in list_products(db, make_user(db))}
    assert AGENT_SKU not in listed
    assert "svc_voice_clone_299" not in listed
    # Real, visible packages (including the new entry tier) stay listed.
    assert NEWCOMER in listed
    assert "pkg_starter_9" in listed


def test_hidden_skus_still_purchasable_by_sku(db):
    """Hiding a SKU from the pricing page must not break direct purchase."""
    u = make_user(db)
    quote = get_price(db, AGENT_SKU, u)
    assert quote.amount_fen == 999900
    assert quote.eligible is True

    # Even the retired voice SKU remains resolvable if a live DB still has it.
    if db.query(Product).filter_by(sku="svc_voice_clone_299").first() is None:
        db.add(
            Product(
                sku="svc_voice_clone_299",
                name="专属音色复刻",
                type="service",
                amount_fen=29900,
                credits=0,
                bonuses={},
                eligibility={},
                sort=5,
            )
        )
        db.commit()
    assert get_price(db, "svc_voice_clone_299", u).amount_fen == 29900


def test_pricing_page_shows_free_banner(db, client):
    r = client.get("/pricing")
    assert r.status_code == 200, r.text
    assert "100 积分" in r.text
    assert "免费" in r.text
