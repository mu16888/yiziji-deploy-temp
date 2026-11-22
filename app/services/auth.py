"""Authentication service: adapters, JWT tokens, dependency guards.

Note: JWT is encoded with HS256 using only the Python standard library so the
template keeps zero extra token dependencies. ``decode_token`` returns ``None``
on any invalid/expired token rather than raising.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid

from fastapi import Depends, HTTPException
from fastapi import Request as FastAPIRequest
from sqlalchemy.orm import Session

from app.adapters.identity import mock as mock_adapter
from app.adapters.identity import password as password_adapter
from app.adapters.identity import wechat as wechat_adapter
from app.config import settings
from app.db import get_db
from app.domain.identity import role_satisfies
from app.domain.ledger import append_entry
from app.models import User

COOKIE_NAME = "access_token"
CSRF_COOKIE_NAME = "csrf_token"
TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60

_PROVIDERS = {
    "password": password_adapter,
    "wechat": wechat_adapter,
    "mock": mock_adapter,
}


class AuthError(Exception):
    """Raised for invalid credentials or unknown providers."""


# --- Minimal, dependency-free HS256 JWT codec -------------------------------


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def encode_token(payload: dict) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    head = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{head}.{body}".encode("ascii")
    signature = hmac.new(
        settings.secret_key.encode(), signing_input, hashlib.sha256
    ).digest()
    return f"{head}.{body}.{_b64url_encode(signature)}"


def decode_token(token: str) -> dict | None:
    try:
        head, body, signature = token.split(".")
        signing_input = f"{head}.{body}".encode("ascii")
        expected = hmac.new(
            settings.secret_key.encode(), signing_input, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64url_decode(signature)):
            return None
        if json.loads(_b64url_decode(head)).get("alg") != "HS256":
            return None
        payload = json.loads(_b64url_decode(body))
        if "exp" in payload and float(payload["exp"]) < time.time():
            return None
        return payload
    except Exception:
        return None


def issue_token(
    user: User,
    *,
    channel: str = "web",
    referrer_code: str | None = None,
) -> str:
    """Return a signed JWT whose payload carries sub/channel/referrer_code."""
    code = (
        referrer_code
        if referrer_code is not None
        else (getattr(user, "referrer_code", None) or "")
    )
    now = int(time.time())
    return encode_token(
        {
            "sub": str(user.id),
            "channel": channel,
            "referrer_code": code,
            "iat": now,
            "exp": now + TOKEN_TTL_SECONDS,
        }
    )


def _user_from_token(token: str, db: Session) -> User | None:
    payload = decode_token(token)
    if not payload:
        return None
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


# --- Public service operations ----------------------------------------------


def authenticate(
    db: Session,
    provider: str,
    credential: str,
    *,
    subject: str | None = None,
) -> User:
    """Resolve a user through the named identity adapter."""
    adapter = _PROVIDERS.get(provider)
    if adapter is None:
        raise AuthError(f"unknown identity provider: {provider}")
    user = adapter.authenticate(db, credential, subject=subject)
    if user is None:
        raise AuthError("invalid credentials")
    if not user.is_active:
        raise AuthError("inactive account")
    return user


def register_user(
    db: Session,
    *,
    phone: str,
    code: str,
    referrer: str | None = None,
) -> User:
    """Register a phone account, grant the 100 signup credits, commit."""
    if not password_adapter.verify_code(code):
        raise AuthError("invalid verification code")

    user = db.query(User).filter_by(phone=phone).first()
    if user is None:
        user = User(
            phone=phone,
            display_name=phone,
            role="user",
            is_active=True,
            invite_code="TCH-" + uuid.uuid4().hex[:6].upper(),
        )
        db.add(user)
        db.flush()

    append_entry(
        db,
        user_id=user.id,
        delta=100,
        entry_type="grant",
        ref_type="signup",
        ref_id=str(user.id),
        idempotency_key=f"signup-grant:{user.id}",
    )
    db.commit()
    return user


# --- FastAPI dependencies ---------------------------------------------------


def get_current_user(
    request: FastAPIRequest,
    db: Session = Depends(get_db),
) -> User:
    """Resolve the caller from the httpOnly Cookie first, Bearer JWT second."""
    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token:
        user = _user_from_token(cookie_token, db)
        if user is not None:
            return user

    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        user = _user_from_token(auth_header[7:].strip(), db)
        if user is not None:
            return user

    raise HTTPException(status_code=401, detail="Not authenticated")


def require_roles(*roles: str):
    """Dependency factory enforcing admin >= agent >= promoter >= user."""

    def _guard(user: User = Depends(get_current_user)) -> User:
        if not any(role_satisfies(user.role, required) for required in roles):
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user

    return _guard


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)
