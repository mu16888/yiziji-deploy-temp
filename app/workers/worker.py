"""DB-backed generation worker.

``poll_once`` advances queued jobs (submit) and running jobs (status -> settle
or refund) in a single pass and is directly callable in tests -- no separate
process or real sleeps needed. ``run_worker`` is the long-running loop that
wakes every ``WORKER_POLL_SEC``.
"""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.adapters.generation import GenerationAdapter, get_adapter
from app.config import settings
from app.db import SessionLocal
from app.domain.generation import (
    FAILED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    CANCELLED,
    complete_job,
    fail_job,
    normalize_status,
    submit_job,
)
from app.models import GenerationJob


def _fetch_result(adapter: GenerationAdapter, job: GenerationJob) -> dict:
    """Fetch the finished artifact; adapters may expose an optional ``result``."""
    fetch = getattr(adapter, "result", None)
    if callable(fetch):
        try:
            value = fetch(job.external_id)
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    code = f"S{int(job.id):06d}"
    return {
        "share_code": code,
        "share_url": f"/c/{code}",
        "credits": int(job.reserved_credits or 0),
    }


def _actual_credits(result: dict, job: GenerationJob) -> int:
    reserved = int(job.reserved_credits or 0)
    raw = result.get("credits")
    if raw is None:
        return reserved
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return reserved


def poll_once(db: Session, adapter: GenerationAdapter | None = None) -> int:
    """Submit queued jobs, then reconcile running jobs; return jobs advanced."""
    adapter = adapter or get_adapter()
    advanced = 0

    queued = (
        db.query(GenerationJob)
        .filter_by(status=QUEUED)
        .order_by(GenerationJob.id)
        .all()
    )
    for job in queued:
        try:
            submit_job(db, job_id=job.id, adapter=adapter)
            advanced += 1
        except Exception:
            # Engine unavailable: leave the job queued for the next poll.
            continue

    running = (
        db.query(GenerationJob)
        .filter_by(status=RUNNING)
        .order_by(GenerationJob.id)
        .all()
    )
    for job in running:
        try:
            state = normalize_status(adapter.status(job.external_id))
        except Exception:
            continue
        if state == SUCCEEDED:
            result = _fetch_result(adapter, job)
            complete_job(
                db,
                job_id=job.id,
                actual_credits=_actual_credits(result, job),
                result=result,
            )
            advanced += 1
        elif state in (FAILED, CANCELLED):
            fail_job(db, job_id=job.id)
            advanced += 1

    return advanced


def run_worker(
    *,
    poll_sec: float | None = None,
    stop=None,
    db_factory=SessionLocal,
    adapter: GenerationAdapter | None = None,
) -> None:
    """Run the poller until ``stop()`` returns True (testable loop)."""
    interval = poll_sec if poll_sec is not None else settings.worker_poll_sec
    while True:
        if stop is not None and stop():
            return
        db = db_factory()
        try:
            poll_once(db, adapter=adapter)
        finally:
            db.close()
        if stop is not None and stop():
            return
        time.sleep(interval)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Poll generation jobs")
    parser.add_argument("--once", action="store_true", help="run a single poll and exit")
    args = parser.parse_args()

    if args.once:
        session = SessionLocal()
        try:
            print(f"advanced={poll_once(session)}")
        finally:
            session.close()
    else:
        run_worker()
