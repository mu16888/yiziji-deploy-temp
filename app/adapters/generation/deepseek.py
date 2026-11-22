"""Self-owned generation adapter: orchestration -> worker generation pipeline.

This is the bridge between the pure ``build_courseware`` orchestration (N3) and
the DB-backed worker. It is intentionally thin: it owns no credit moves and no
job state transitions, only an in-memory registry keyed by ``external_id``.

The LLM client is injectable so tests never touch the network. When omitted, a
real :class:`~app.adapters.llm.deepseek.DeepSeekClient` is built from settings
(key from the environment). Estimation is a LOCAL decision (design §12.6) and
never calls the model.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from app.adapters.generation.mock import MockGenerationAdapter
from app.adapters.llm import LLMClient
from app.adapters.llm.deepseek import DeepSeekClient
from app.config import settings
from app.domain.courseware import CoursewareError, build_courseware

if TYPE_CHECKING:
    from app.models import GenerationJob

logger = logging.getLogger(__name__)

_SUCCEEDED = "succeeded"
_FAILED = "failed"
_CANCELLED = "cancelled"
# A registry miss must never look finished: the worker would settle an empty
# artifact and charge the customer (e.g. after a worker restart with a fresh
# store, or with multiple workers sharing one DB). "running" is non-settling.
_RUNNING = "running"

# A short, didactic fallback so the demo can run without an uploaded lecture.
_DEFAULT_MATERIAL = (
    "本节课学习一元二次方程。形如 ax²+bx+c=0（a≠0）的方程叫做一元二次方程。"
    "解法包括直接开平方法、配方法、公式法和因式分解法。"
    "求根公式为 x=(-b±√(b²-4ac))/(2a)，判别式 Δ=b²-4ac 决定根的情况。"
)

# Estimate is a pure local rule; delegate to the already-tested mock rules so
# the two adapters price specs identically. No LLM is ever consulted.
_ESTIMATOR = MockGenerationAdapter()


class DeepSeekGenerationAdapter:
    """Run ``build_courseware`` on submit and cache its result for worker polls."""

    def __init__(
        self,
        client: LLMClient | None = None,
        store: dict | None = None,
    ) -> None:
        if client is None:
            client = DeepSeekClient(http_client=httpx.Client(timeout=120))
        self.client = client
        self._store = store if store is not None else {}

    # --- GenerationAdapter ----------------------------------------------------

    def estimate(self, spec: dict) -> int:
        """Predicted credits per design §12.6 -- local rules only, no LLM call."""
        return _ESTIMATOR.estimate(spec or {})

    def submit(self, job: "GenerationJob", spec: dict) -> str:
        """Generate the courseware now and cache it; return ``ds-{job.id}``.

        A fatal orchestration failure is recorded as ``failed`` rather than
        raised, so the worker can advance the job to RUNNING and then refund via
        ``fail_job`` on the next poll (a raised error here would leave the job
        stuck in ``queued`` with credits reserved forever).
        """
        # Compute a non-raising id first so even a malformed job/spec can be
        # recorded as failed rather than escaping and leaving credits reserved.
        external_id = f"ds-{getattr(job, 'id', '')}"
        try:
            spec = spec or {}
            text = spec.get("material_text") or spec.get("text") or _DEFAULT_MATERIAL
            courseware = build_courseware(
                str(text),
                self.client,
                subject=str(spec.get("subject") or ""),
                grade=str(spec.get("grade") or ""),
            )
            share_code = f"D{int(job.id):06d}"
            result = dict(courseware)
            result["share_code"] = share_code
            result["share_url"] = f"/c/{share_code}"
            if result.get("credits") is None:
                result["credits"] = int(getattr(job, "reserved_credits", 0) or 0)
        except Exception:  # noqa: BLE001 - never leave the job stuck queued
            logger.warning(
                "DeepSeek courseware generation failed for %s; marking failed",
                external_id,
                exc_info=True,
            )
            self._store[external_id] = {"_status": _FAILED}
            return external_id

        self._store[external_id] = result
        return external_id

    def status(self, external_id: str) -> str:
        """Report the cached state; an unknown id is ``running``, never done.

        Defaulting to "succeeded" let ``poll_once`` settle a job whose registry
        entry had been lost (worker restart / multi-worker), charging the
        customer with an empty result. An explicit membership check keeps such
        jobs non-settling until the owning worker re-submits them.
        """
        entry = self._store.get(str(external_id))
        if entry is None:
            return _RUNNING
        return str(entry.get("_status", _SUCCEEDED))

    def cancel(self, external_id: str) -> None:
        """Mark the cached entry cancelled (no engine-side work to stop)."""
        entry = self._store.setdefault(str(external_id), {})
        entry["_status"] = _CANCELLED

    # --- adapter-specific helpers --------------------------------------------

    def result(self, external_id: str) -> dict | None:
        """Return the finished Courseware, or ``None`` for an unknown id.

        ``worker._fetch_result`` only accepts a dict; returning ``None`` here
        prevents an empty registry miss from masquerading as an artifact. The
        primary guard is ``status() == "running"`` so settle is never attempted.
        """
        entry = self._store.get(str(external_id))
        return dict(entry) if entry is not None else None
