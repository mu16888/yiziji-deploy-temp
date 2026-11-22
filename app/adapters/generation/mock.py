"""Zero-dependency mock generation adapter for local runs and tests.

Estimation follows design §12.6. ``submit`` returns an external id immediately,
``status`` reports ``succeeded`` and ``result`` carries the share code/link.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import GenerationJob

_SUCCEEDED = "succeeded"
_FAILED = "failed"
_CANCELLED = "cancelled"

_EXPORT_KINDS = frozenset({"pptx", "offline_html", "html", "export"})
_EXPORT_FORMATS = frozenset({"pptx", "html", "offline_html"})
_PBL_KINDS = frozenset({"pbl", "project", "project_based", "project_based_learning"})
# Types the demo UI (templates/generate.html) submits for its priced tiers.
_INTERACTIVE_TYPES = frozenset(
    {"interactive", "quiz", "interactive_classroom_with_quiz"}
)
_INTERACTIVE_FLAGS = (
    "interactive_sim",
    "interactive",
    "simulation",
    "quiz",
    "has_quiz",
    "with_quiz",
)


class MockGenerationAdapter:
    """In-memory engine; keeps one result per external id per instance."""

    def __init__(self) -> None:
        self._registry: dict[str, dict] = {}
        self._status: dict[str, str] = {}

    # --- GenerationAdapter ----------------------------------------------------

    def estimate(self, spec: dict) -> int:
        """Predicted credits per design §12.6 (export 5 / PBL 40 / interactive 25 / basic 10)."""
        spec = spec or {}
        kind = str(spec.get("type", "") or "").lower().strip()
        fmt = str(spec.get("format", "") or "").lower().strip()
        export = spec.get("export")

        if (
            kind in _EXPORT_KINDS
            or "export" in kind
            or fmt in _EXPORT_FORMATS
            or export in _EXPORT_FORMATS
        ):
            return 5
        if kind in _PBL_KINDS:
            return 40
        if kind in _INTERACTIVE_TYPES or any(
            bool(spec.get(flag)) for flag in _INTERACTIVE_FLAGS
        ):
            return 25
        return 10

    def submit(self, job: "GenerationJob", spec: dict) -> str:
        external_id = f"mock-{int(job.id)}"
        share_code = f"S{int(job.id):06d}"
        self._registry[external_id] = {
            "share_code": share_code,
            "share_url": f"/c/{share_code}",
            "credits": int(getattr(job, "reserved_credits", 0) or 0),
            "status": _SUCCEEDED,
        }
        self._status[external_id] = _SUCCEEDED
        return external_id

    def status(self, external_id: str) -> str:
        return self._status.get(str(external_id), _SUCCEEDED)

    def cancel(self, external_id: str) -> None:
        self._status[str(external_id)] = _CANCELLED

    # --- adapter-specific helpers --------------------------------------------

    def result(self, external_id: str) -> dict:
        """Return the finished artifact (including ``share_code``)."""
        result = self._registry.get(str(external_id))
        if result is None:
            code = f"S-{abs(hash(str(external_id))) % 1_000_000:06d}"
            result = {"share_code": code, "share_url": f"/c/{code}", "credits": 0}
        return dict(result)

    def mark_failed(self, external_id: str) -> None:
        """Test helper: simulate the engine reporting a failed job."""
        self._status[str(external_id)] = _FAILED
