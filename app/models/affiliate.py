"""Invitations, attribution, promoters, agents and their settlement ledgers."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import IdMixin, IdempotencyMixin, TimestampMixin


class Invitation(IdMixin, TimestampMixin, Base):
    __tablename__ = "invitations"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    channel: Mapped[str | None] = mapped_column(String, nullable=True)
    click_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Attribution(IdMixin, TimestampMixin, Base):
    """90-day candidate relationship, locked by an explicit confirm + order."""

    __tablename__ = "attributions"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), unique=True, nullable=False, index=True
    )
    inviter_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    code: Mapped[str | None] = mapped_column(String, nullable=True)
    candidate_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    promoter_id: Mapped[int | None] = mapped_column(
        ForeignKey("promoters.id"), nullable=True
    )
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)


class Promoter(IdMixin, TimestampMixin, Base):
    __tablename__ = "promoters"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), unique=True, nullable=False, index=True
    )
    level: Mapped[str] = mapped_column(
        String, default="basic", nullable=False, index=True
    )
    code: Mapped[str | None] = mapped_column(String, unique=True, nullable=True, index=True)
    invited_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Agent(IdMixin, TimestampMixin, Base):
    __tablename__ = "agents"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), unique=True, nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String, default="pending", nullable=False, index=True
    )
    admission_order_no: Mapped[str | None] = mapped_column(String, nullable=True)
    fund_fen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentFundEntry(IdMixin, IdempotencyMixin, TimestampMixin, Base):
    """Non-withdrawable agency credit ledger; only usable for agency orders."""

    __tablename__ = "agent_fund_ledger"

    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id"), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    delta_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    ref_type: Mapped[str | None] = mapped_column(String, nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String, nullable=True)
    remark: Mapped[str | None] = mapped_column(String, nullable=True)


class CommissionEntry(IdMixin, IdempotencyMixin, TimestampMixin, Base):
    """Pre-tax commission with an observation window and reversal state."""

    __tablename__ = "commission_ledger"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    order_no: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    promoter_id: Mapped[int | None] = mapped_column(
        ForeignKey("promoters.id"), nullable=True
    )
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String, default="pending", nullable=False, index=True
    )
    available_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ref_type: Mapped[str | None] = mapped_column(String, nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String, nullable=True)


class Withdrawal(IdMixin, IdempotencyMixin, TimestampMixin, Base):
    __tablename__ = "withdrawals"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String, default="pending", nullable=False, index=True
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remark: Mapped[str | None] = mapped_column(String, nullable=True)
