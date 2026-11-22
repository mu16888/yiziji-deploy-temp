"""Generation job lifecycle: estimate -> reserve -> queue -> run -> settle/refund.

Pure domain/service module (no FastAPI). Credit moves happen inside the caller's
transaction via the deterministic-key ledger primitives, so retries never
double-charge. Job states: queued -> running -> succeeded / failed / cancelled.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.adapters.generation import GenerationAdapter, get_adapter
from app.domain.ledger import InsufficientCredits, append_entry, reserve, settle
from app.models import GenerationJob
from app.services.compliance import mark_aigc

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

_ALLOWED_TRANSITIONS = {
    QUEUED: {RUNNING, SUCCEEDED, FAILED, CANCELLED},
    RUNNING: {SUCCEEDED, FAILED, CANCELLED},
    SUCCEEDED: set(),
    FAILED: set(),
    CANCELLED: set(),
}

_STATUS_ALIASES = {
    "pending": QUEUED,
    "submitted": QUEUED,
    "queued": QUEUED,
    "running": RUNNING,
    "processing": RUNNING,
    "in_progress": RUNNING,
    "success": SUCCEEDED,
    "succeeded": SUCCEEDED,
    "complete": SUCCEEDED,
    "completed": SUCCEEDED,
    "done": SUCCEEDED,
    "finished": SUCCEEDED,
    "failure": FAILED,
    "failed": FAILED,
    "error": FAILED,
    "errored": FAILED,
    "cancelled": CANCELLED,
    "canceled": CANCELLED,
}


class GenerationError(Exception):
    """Base class for generation service errors."""


class InvalidJobState(GenerationError):
    """Raised for an illegal job state transition."""


class InsufficientCreditsForJob(GenerationError):
    """Raised when the reservation cannot be funded."""


class InvalidEstimate(GenerationError):
    """Raised when an adapter returns a non-positive / non-integer estimate."""


def assert_transition(current: str, target: str) -> None:
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise InvalidJobState(f"cannot transition {current!r} -> {target!r}")


def normalize_status(raw: str) -> str:
    """Map an engine-specific status string onto a canonical job state."""
    return _STATUS_ALIASES.get(str(raw or "").strip().lower(), QUEUED)


def get_job(db: Session, job_id: int) -> GenerationJob:
    job = db.get(GenerationJob, int(job_id))
    if job is None:
        raise GenerationError(f"unknown generation job: {job_id}")
    return job


def _validate_estimate(value) -> int:
    if isinstance(value, bool) or value is None:
        raise InvalidEstimate(f"invalid credit estimate: {value!r}")
    try:
        amount = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidEstimate(f"invalid credit estimate: {value!r}") from exc
    if amount <= 0:
        raise InvalidEstimate("credit estimate must be positive")
    return amount


def _validate_actual(value) -> int:
    if isinstance(value, bool) or value is None:
        raise InvalidEstimate(f"invalid actual credits: {value!r}")
    try:
        amount = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidEstimate(f"invalid actual credits: {value!r}") from exc
    if amount < 0:
        raise InvalidEstimate("actual credits must be non-negative")
    return amount


def create_job(
    db: Session, *, user_id: int, spec: dict, adapter: GenerationAdapter | None = None
) -> GenerationJob:
    """Estimate, reserve credits and enqueue a job (status ``queued``).

    ``adapter`` is injectable for deterministic tests; when omitted it is
    resolved from ``GENERATION_ADAPTER``. The job reference stored in
    ``idempotency_key`` drives all downstream deterministic ledger keys
    (``reserve:{job_ref}``, ``settle:{job_id}``, ``refund:{job_id}``).
    """
    adapter = adapter or get_adapter()
    estimated = _validate_estimate(adapter.estimate(spec))
    job_ref = f"job:{uuid.uuid4().hex[:16]}"

    try:
        with db.begin_nested():
            job = GenerationJob(
                user_id=int(user_id),
                spec=dict(spec),
                status=QUEUED,
                reserved_credits=estimated,
                idempotency_key=job_ref,
            )
            db.add(job)
            db.flush()
            try:
                reserve(
                    db,
                    user_id=int(user_id),
                    amount=estimated,
                    ref_type="job",
                    ref_id=job_ref,
                    idempotency_key=f"reserve:{job_ref}",
                )
            except InsufficientCredits as exc:
                raise InsufficientCreditsForJob(str(exc)) from exc
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(job)
    return job


def submit_job(
    db: Session, *, job_id: int, adapter: GenerationAdapter | None = None
) -> GenerationJob:
    """Submit a queued job to the engine and move it to ``running``."""
    adapter = adapter or get_adapter()
    job = get_job(db, job_id)
    if job.status == RUNNING:
        return job
    if job.status != QUEUED:
        raise InvalidJobState(f"cannot submit job in status {job.status!r}")

    external_id = adapter.submit(job, job.spec)
    if not external_id:
        raise GenerationError("adapter returned an empty external_id")

    try:
        assert_transition(job.status, RUNNING)
        job.external_id = str(external_id)
        job.status = RUNNING
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(job)
    return job


def complete_job(
    db: Session,
    *,
    job_id: int,
    actual_credits: int,
    result: dict | None,
) -> GenerationJob:
    """Settle the reservation against real usage (多退少补) and mark succeeded."""
    job = get_job(db, job_id)
    if job.status not in (QUEUED, RUNNING):
        raise InvalidJobState(f"cannot complete job in status {job.status!r}")
    assert_transition(job.status, SUCCEEDED)

    actual = _validate_actual(actual_credits)
    marked = mark_aigc(dict(result or {}))

    try:
        with db.begin_nested():
            settle(
                db,
                user_id=job.user_id,
                reserved_ref_id=job.idempotency_key,
                actual_amount=actual,
                idempotency_key=f"settle:{job.id}",
            )
            job.status = SUCCEEDED
            job.actual_credits = actual
            job.result = marked
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(job)
    return job


def fail_job(db: Session, *, job_id: int) -> GenerationJob:
    """Refund the full reservation and mark the job ``failed`` (idempotent)."""
    job = get_job(db, job_id)
    if job.status == FAILED:
        return job  # replay: refund row already exists
    if job.status not in (QUEUED, RUNNING):
        raise InvalidJobState(f"cannot fail job in status {job.status!r}")
    assert_transition(job.status, FAILED)

    try:
        with db.begin_nested():
            append_entry(
                db,
                user_id=job.user_id,
                delta=int(job.reserved_credits or 0),
                entry_type="refund",
                ref_type="job",
                ref_id=job.idempotency_key,
                idempotency_key=f"refund:{job.id}",
            )
            job.status = FAILED
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(job)
    return job
