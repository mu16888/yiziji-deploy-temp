"""Student share links: resolve a share code to a job and enforce its TTL.

Pure service (no FastAPI imports). A ``GenerationJob.result`` carries the
``share_code`` produced by the worker/adapter. Link validity is either an
explicit ``result['expires_at']`` (ISO string or datetime) or, by default, the
job's last-update time plus ``SHARE_LINK_TTL_DAYS``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models import GenerationJob
from app.models.base import utcnow


def _naive(value) -> datetime | None:
    """Coerce an ISO string / datetime to a naive UTC datetime."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def find_job_by_share_code(db: Session, share_code: str) -> GenerationJob | None:
    """Return the succeeded job whose result carries ``share_code`` (or None)."""
    if not share_code:
        return None
    target = str(share_code)
    for job in (
        db.query(GenerationJob)
        .filter(GenerationJob.result.isnot(None))
        .order_by(GenerationJob.id.desc())
        .all()
    ):
        if str((job.result or {}).get("share_code", "")) == target:
            return job
    return None


def share_expires_at(job: GenerationJob) -> datetime:
    """The moment the classroom link stops being valid."""
    explicit = _naive((job.result or {}).get("expires_at"))
    if explicit is not None:
        return explicit
    anchor = job.updated_at or job.created_at or utcnow()
    return _naive(anchor) + timedelta(days=int(settings.share_link_ttl_days))


def is_share_expired(job: GenerationJob, *, now: datetime | None = None) -> bool:
    now = _naive(now) or utcnow()
    return now >= share_expires_at(job)
