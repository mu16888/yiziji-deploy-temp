"""Product pages and the no-login student share link (Task 10).

The three Review-Focus tests are kept by name. ``client`` is anonymous by
default; teacher pages must gate on login while the student classroom must not.
"""

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.domain.ledger import append_entry
from app.main import app
from app.models import GenerationJob, Product, User
from app.models.base import utcnow
from app.services.auth import issue_token
from scripts.seed import PRODUCTS


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
    teacher = User(
        phone="13800000001",
        display_name="演示老师",
        role="user",
        is_active=True,
        invite_code="TCH-TEST1",
    )
    session.add(teacher)
    session.flush()
    append_entry(
        session,
        user_id=teacher.id,
        delta=100,
        entry_type="grant",
        ref_type="signup",
        ref_id=str(teacher.id),
        idempotency_key=f"signup-grant:{teacher.id}",
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


@pytest.fixture()
def teacher(db):
    return db.query(User).one()


def _login(client, teacher):
    client.cookies.set("access_token", issue_token(teacher, channel="web"))


def _make_job(db, user, share_code, **extra):
    result = {"share_code": share_code, "share_url": f"/c/{share_code}"}
    result.update(extra.pop("result_extra", {}))
    job = GenerationJob(
        user_id=user.id,
        spec={"topic": "分数运算", "type": "interactive"},
        status="succeeded",
        result=result,
        **extra,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


# --- Review Focus tests (brief, verbatim names) ------------------------------


def test_student_classroom_needs_no_login(db, client, teacher):
    """A shared classroom renders for an anonymous visitor (no cookie)."""
    _make_job(db, teacher, "S123")
    assert client.get("/c/S123").status_code == 200
    # No auth cookie was set: prove the page is genuinely public.
    assert "access_token" not in client.cookies


def test_share_link_expiry(db, client, teacher):
    """Unexpired link -> 200; force expires_at into the past -> 404/410."""
    job = _make_job(
        db,
        teacher,
        "S124",
        result_extra={"expires_at": (utcnow() + timedelta(days=1)).isoformat()},
    )
    assert client.get("/c/S124").status_code == 200

    job.result = {**job.result, "expires_at": (utcnow() - timedelta(days=1)).isoformat()}
    db.add(job)
    db.commit()
    assert client.get("/c/S124").status_code in (404, 410)


def test_teacher_pages_require_login(client):
    paths = (
        "/onboarding",
        "/workbench",
        "/generate",
        "/library",
        "/history",
        "/promote",
        "/org",
        "/jobs/1",
        "/classroom/1/edit",
        "/classroom/1/share",
    )
    for path in paths:
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (302, 401), (path, r.status_code)


# --- page rendering ----------------------------------------------------------


def test_public_pages_render(client):
    for path in ("/", "/login", "/pricing"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text[:200])


def test_all_teacher_pages_render_when_logged_in(client, teacher):
    _login(client, teacher)
    for path in (
        "/onboarding",
        "/workbench",
        "/generate",
        "/library",
        "/history",
        "/promote",
        "/org",
    ):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text[:200])


def test_generate_page_shows_server_estimate_and_balance(client, teacher):
    _login(client, teacher)
    r = client.get("/generate")
    assert r.status_code == 200, r.text[:200]
    # estimate + balance come from the server, never hard-coded in the template.
    assert "10" in r.text  # default estimate from the adapter
    assert "100" in r.text  # signup-credits balance for a fresh user


def test_job_and_classroom_pages_render_for_owner(db, client, teacher):
    _login(client, teacher)
    job = _make_job(db, teacher, "S125")
    for path in (
        f"/jobs/{job.id}",
        f"/classroom/{job.id}/edit",
        f"/classroom/{job.id}/share",
    ):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text[:200])


def test_unknown_share_code_is_404(client):
    assert client.get("/c/NO_SUCH_CODE").status_code == 404


def test_default_ttl_expires_old_job(db, client, teacher):
    """With no explicit expires_at, expiry = updated_at + SHARE_LINK_TTL_DAYS."""
    job = _make_job(db, teacher, "S126")
    job.updated_at = utcnow() - timedelta(days=30)
    db.add(job)
    db.commit()
    assert client.get("/c/S126").status_code in (404, 410)


def test_no_hardcoded_price_literals_in_templates():
    forbidden = (
        "19900", "69900", "199900", "399900", "29900", "999900",
        "55920", "¥699", "¥199", "¥9999", "699.00",
    )
    for src in Path("templates").glob("*.html"):
        text = src.read_text(encoding="utf-8")
        for literal in forbidden:
            assert literal not in text, (src.name, literal)


def test_org_route_denies_non_owner_and_redacts_pii(db, client, teacher):
    """Final review Critical #1: /org must not leak other members' PII.

    A logged-in, non-owner ``user`` sharing an org_id gets 403 (no member
    enumeration). The org owner sees full phone/balance. A staff member
    (agent/admin) may view the org but other members' phone/balance are
    redacted unless the caller is the owner.
    """
    owner = teacher
    owner.org_id = f"ORG-U{owner.id}"
    member = User(
        phone="13900009999", display_name="成员B", role="user",
        org_id=owner.org_id, is_active=True, invite_code="ORG-MEMBER-B",
    )
    staff = User(
        phone="13700007777", display_name="代理C", role="agent",
        org_id=owner.org_id, is_active=True, invite_code="ORG-STAFF-C",
    )
    db.add_all([member, staff])
    db.commit()
    append_entry(
        db, user_id=member.id, delta=555, entry_type="grant",
        ref_type="signup", ref_id=str(member.id),
        idempotency_key=f"signup-grant:{member.id}",
    )
    db.commit()

    # A non-owner normal user in the same org is denied outright.
    _login(client, member)
    denied = client.get("/org", follow_redirects=False)
    assert denied.status_code in (403, 404)

    # The owner sees full PII (phone + per-member balance) for everyone.
    _login(client, owner)
    owned = client.get("/org")
    assert owned.status_code == 200, owned.text[:200]
    assert member.phone in owned.text
    assert "555" in owned.text  # member's credit balance

    # Staff can view, but other members' phone/balance are redacted.
    _login(client, staff)
    redacted = client.get("/org")
    assert redacted.status_code == 200, redacted.text[:200]
    assert member.phone not in redacted.text
    assert "555" not in redacted.text
    assert "***" in redacted.text  # explicit redaction placeholder
