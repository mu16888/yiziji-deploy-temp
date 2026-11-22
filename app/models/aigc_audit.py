"""AIGC labeling / compliance audit records (retention configured in settings)."""

from sqlalchemy import Boolean, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import IdMixin, TimestampMixin


class AigcAudit(IdMixin, TimestampMixin, Base):
    __tablename__ = "aigc_audit"

    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("generation_jobs.id"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    aigc_label: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
