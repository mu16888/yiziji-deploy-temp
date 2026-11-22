"""Phone/email + verification-code identity adapter.

Development mode accepts the fixed code from ``VERIFY_CODE_DEV``. In a real
deployment this adapter would send/verify a code via an SMS or mail gateway;
that gateway is deliberately outside the template.
"""

import secrets

from app.config import settings


def verify_code(code: str) -> bool:
    """Development verification: accept exactly VERIFY_CODE_DEV."""
    return bool(code) and secrets.compare_digest(str(code), settings.verify_code_dev)


def authenticate(db, credential: str, *, subject: str | None = None):
    """Validate ``credential`` and resolve the user identified by ``subject``.

    ``subject`` is the phone (or email) supplied alongside the code. Without a
    subject there is no account to resolve, so authentication fails.
    """
    from app.models import User

    if not verify_code(credential) or not subject:
        return None
    return db.query(User).filter_by(phone=subject).first()
