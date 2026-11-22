"""Orders and their immutable event stream (webhook idempotency lives here)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import IdMixin, IdempotencyMixin, TimestampMixin


class Order(IdMixin, IdempotencyMixin, TimestampMixin, Base):
    __tablename__ = "orders"

    no: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )
    sku: Mapped[str] = mapped_column(String, nullable=False, index=True)

    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_amount_fen: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(
        String, default="created", nullable=False, index=True
    )
    channel: Mapped[str | None] = mapped_column(String, nullable=True)
    refund_status: Mapped[str | None] = mapped_column(String, nullable=True)

    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="orders")
    events: Mapped[list["OrderEvent"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderEvent(IdMixin, IdempotencyMixin, TimestampMixin, Base):
    __tablename__ = "order_events"
    __table_args__ = (
        UniqueConstraint("order_no", "event_id", name="uq_order_event"),
    )

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id"), nullable=False, index=True
    )
    order_no: Mapped[str] = mapped_column(String, nullable=False, index=True)
    event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    order: Mapped["Order"] = relationship(back_populates="events")
