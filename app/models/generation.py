"""Generation jobs: reserve credits first, then settle or refund."""

from sqlalchemy import ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import IdMixin, IdempotencyMixin, TimestampMixin


class GenerationJob(IdMixin, IdempotencyMixin, TimestampMixin, Base):
    __tablename__ = "generation_jobs"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String, default="queued", nullable=False, index=True
    )
    external_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    reserved_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)

    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
