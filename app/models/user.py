"""Identity and role models."""

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import IdMixin, TimestampMixin


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    phone: Mapped[str | None] = mapped_column(String, unique=True, nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    wechat_openid: Mapped[str | None] = mapped_column(
        String, unique=True, nullable=True, index=True
    )
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)

    role: Mapped[str] = mapped_column(String, default="user", nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Optional anti-self-referral signals (hashed, never plaintext).
    identity_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    org_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    invite_code: Mapped[str | None] = mapped_column(
        String, unique=True, nullable=True, index=True
    )

    credit_entries: Mapped[list["CreditEntry"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    balance: Mapped["CreditBalance | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class Role(IdMixin, TimestampMixin, Base):
    """Role catalogue: user -> promoter -> agent -> admin."""

    __tablename__ = "user_roles"

    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
