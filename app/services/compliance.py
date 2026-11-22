"""AIGC compliance marking and audit retention.

Marking is metadata-only: the generation adapter returns a result dict, not a
media file, so the design's "video start" label and MP4 ``moov.udta.meta``
implicit AIGC marker are represented as result metadata. No media binary is
stamped.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AigcAudit
from app.models.base import utcnow
from app.services.admin import jsonable

AIGC_MARKER = "AIGC"
AIGC_LABEL_TEXT = "AI辅助生成"
AIGC_GENERATE_ACTION = "aigc:generate"


def mark_aigc(result: dict) -> dict:
    """Add the AIGC label to a generation/export result (metadata only).

    A label is added only when ``AIGC_LABEL`` is enabled; any existing ``meta``
    mapping is preserved. Calling twice is idempotent (T7 settles, T10 renders).
    """
    out = dict(result)
    enabled = bool(settings.aigc_label)
    out["aigc_label"] = enabled
    if enabled:
        meta = out.get("meta")
        if not isinstance(meta, dict):
            meta = {"original": meta} if meta is not None else {}
        else:
            meta = dict(meta)
        meta[AIGC_MARKER] = AIGC_LABEL_TEXT  # implicit marker, cf. moov.udta.meta
        meta["label"] = AIGC_LABEL_TEXT  # visible label, cf. video start
        out["meta"] = meta
    return out


def record_audit(
    db: Session,
    *,
    actor_id: int | None,
    action: str,
    payload: dict,
) -> AigcAudit:
    """Append an AIGC audit row; flush but do not commit (stays transactional).

    ``payload`` is made JSON-safe (datetimes -> ISO), matching the admin audit
    convention for the table's JSON column.
    """
    row = AigcAudit(
        actor_id=actor_id,
        action=action,
        payload=jsonable(payload),
        aigc_label=bool(settings.aigc_label),
    )
    db.add(row)
    db.flush()
    return row


def purge_expired(db: Session) -> int:
    """Delete audit rows older than ``AUDIT_RETENTION_DAYS``; return the count."""
    cutoff = utcnow() - timedelta(days=int(settings.audit_retention_days))
    removed = db.execute(
        delete(AigcAudit).where(AigcAudit.created_at < cutoff)
    ).rowcount
    db.commit()
    return int(removed or 0)
