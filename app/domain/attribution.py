"""Pure attribution rules: candidate windows and lock state. No DB, no FastAPI."""

from datetime import datetime, timedelta, timezone

CANDIDATE_WINDOW_DAYS = 90


def utcnow() -> datetime:
    """Naive UTC now — consistent with the rest of the codebase."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def is_candidate_expired(candidate_at: datetime | None, now: datetime | None = None) -> bool:
    """A referral candidate older than 90 days may be re-bound."""
    if candidate_at is None:
        return True
    now = now or utcnow()
    return candidate_at + timedelta(days=CANDIDATE_WINDOW_DAYS) <= now


def is_locked(attribution) -> bool:
    """A locked attribution is the long-term unique owner — never overwritten."""
    return getattr(attribution, "locked_at", None) is not None


def is_confirmed(attribution) -> bool:
    return getattr(attribution, "confirmed_at", None) is not None
