"""Product catalogue. Prices and rules live in the DB, never in the client."""

from sqlalchemy import Boolean, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import IdMixin, TimestampMixin


class Product(IdMixin, TimestampMixin, Base):
    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String, nullable=False, index=True)

    amount_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    original_amount_fen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    bonuses: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    eligibility: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
