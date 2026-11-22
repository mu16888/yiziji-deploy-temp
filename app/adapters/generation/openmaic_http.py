"""HTTP generation adapter that delegates to an external OpenMAIC service.

All calls go to ``OPENMAIC_URL`` (overridable via ``base_url`` for tests) and
follow the same ``GenerationAdapter`` Protocol as the mock. No real service is
required in tests: a client object exposing ``.post()`` / ``.get()`` can be
injected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config import settings

if TYPE_CHECKING:
    from app.models import GenerationJob


class GenerationAdapterError(RuntimeError):
    """Raised when the external engine cannot be reached or returns bad data."""


class OpenMaicHttpAdapter:
    """Talks to OpenMAIC's small JSON API over HTTP."""

    def __init__(self, base_url: str | None = None, client: Any | None = None) -> None:
        self.base_url = (base_url or settings.openmaic_url or "").rstrip("/")
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import httpx

            # Module-level helpers open and close a short-lived client per call,
            # so a long-running worker polling every few seconds cannot leak
            # persistent connections. Tests inject a fake client instead.
            self._client = httpx
        return self._client

    def _url(self, path: str) -> str:
        if not self.base_url:
            raise GenerationAdapterError("OPENMAIC_URL is not configured")
        return f"{self.base_url}{path}"

    @staticmethod
    def _payload(response: Any) -> dict:
        try:
            data = response.json()
        except Exception as exc:  # malformed / non-JSON response
            raise GenerationAdapterError("invalid JSON from OpenMAIC") from exc
        if not isinstance(data, dict):
            raise GenerationAdapterError("OpenMAIC response is not an object")
        return data

    # --- GenerationAdapter ----------------------------------------------------

    def estimate(self, spec: dict) -> int:
        data = self._payload(self.client.post(self._url("/estimate"), json={"spec": spec}))
        credits = data.get("credits")
        if isinstance(credits, bool) or not isinstance(credits, int) or credits <= 0:
            raise GenerationAdapterError("OpenMAIC estimate must be a positive integer")
        return credits

    def submit(self, job: "GenerationJob", spec: dict) -> str:
        data = self._payload(
            self.client.post(
                self._url("/jobs"),
                json={"job_id": int(job.id), "spec": spec},
            )
        )
        external_id = data.get("external_id")
        if not external_id:
            raise GenerationAdapterError("OpenMAIC submit returned no external_id")
        return str(external_id)

    def status(self, external_id: str) -> str:
        data = self._payload(self.client.get(self._url(f"/jobs/{external_id}/status")))
        value = data.get("status")
        if not value:
            raise GenerationAdapterError("OpenMAIC status returned no status")
        return str(value)

    def cancel(self, external_id: str) -> None:
        self.client.post(self._url(f"/jobs/{external_id}/cancel"))

    # --- adapter-specific helpers --------------------------------------------

    def result(self, external_id: str) -> dict:
        """Fetch the finished artifact from OpenMAIC."""
        return self._payload(self.client.get(self._url(f"/jobs/{external_id}/result")))
