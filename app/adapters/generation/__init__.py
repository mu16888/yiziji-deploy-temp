"""Generation adapter contract, shared status helpers and adapter selection.

Adapters are stateless with respect to the database: they estimate usage, submit
a job to an external engine, report its status and cancel it. All credit moves
and state transitions live in ``app.domain.generation``. The only extra method
an adapter may expose is ``result(external_id) -> dict`` (used by the worker to
fetch the finished artifact such as ``share_code``); it is intentionally not
part of the pinned Protocol, so a minimal adapter can omit it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.config import settings

if TYPE_CHECKING:
    from app.models import GenerationJob


@runtime_checkable
class GenerationAdapter(Protocol):
    """The four operations the generation pipeline relies on."""

    def estimate(self, spec: dict) -> int:
        """Return the predicted credit cost for ``spec`` (integer, > 0)."""
        ...

    def submit(self, job: "GenerationJob", spec: dict) -> str:
        """Submit the job to the engine; return its external id."""
        ...

    def status(self, external_id: str) -> str:
        """Return the engine-side status string for ``external_id``."""
        ...

    def cancel(self, external_id: str) -> None:
        """Ask the engine to cancel ``external_id`` (no-op if unsupported)."""
        ...


def get_adapter(name: str | None = None) -> GenerationAdapter:
    """Resolve the configured generation adapter (``mock`` by default)."""
    which = (name or settings.generation_adapter or "mock").lower()
    if which == "mock":
        from app.adapters.generation import mock as adapter

        return adapter.MockGenerationAdapter()
    if which == "openmaic_http":
        from app.adapters.generation import openmaic_http as adapter

        return adapter.OpenMaicHttpAdapter()
    raise ValueError(f"unknown generation adapter: {which}")
