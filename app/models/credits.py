"""Immutable credit ledger and cached balance."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from app.db import Base
from app.models.base import IdMixin, IdempotencyMixin, TimestampMixin


class CreditEntry(IdMixin, IdempotencyMixin, TimestampMixin, Base):
    """Append-only ledger. Balance is derived from the sum of ``delta``."""

    __tablename__ = "credit_ledger"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)

    # Spec §5.2 calls this column ``type``; keep that DB name, expose the
    # friendlier ``entry_type`` attribute used by the ledger service.
    entry_type: Mapped[str] = mapped_column("type", String, nullable=False, index=True)
    type = synonym("entry_type")

    ref_type: Mapped[str | None] = mapped_column(String, nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String, nullable=True)
    pool: Mapped[str] = mapped_column(
        String, default="universal", nullable=False, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)

    user: Mapped["User"] = relationship(back_populates="credit_entries")


class CreditBalance(IdMixin, TimestampMixin, Base):
    """Cached balance per user and pool; reconciled against the ledger daily."""

    __tablename__ = "credit_balance"
    __table_args__ = (UniqueConstraint("user_id", "pool", name="uq_balance_user_pool"),)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    pool: Mapped[str] = mapped_column(
        String, default="universal", nullable=False, index=True
    )
    balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user: Mapped["User"] = relationship(back_populates="balance")
