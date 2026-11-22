"""Shared column mixins for ORM models."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    """Naive UTC timestamp, consistent across SQLite and Postgres."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_idempotency_key() -> str:
    return uuid.uuid4().hex


class IdMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class IdempotencyMixin:
    """Every fund-related table carries a unique, non-null idempotency key.

    A client-side UUID default keeps the DB contract (unique + non-null) while
    letting services that do not take an explicit key still insert a row.
    """

    idempotency_key: Mapped[str] = mapped_column(
        String,
        unique=True,
        nullable=False,
        default=new_idempotency_key,
    )
