"""Thin, injectable DeepSeek chat client with JSON-mode and bounded retries.

No vendor code leaked into the domain: this module is the only place that
knows how to talk to DeepSeek. Callers depend on ``app.adapters.llm.LLMClient``.

Safety: the API key is taken from config (or an explicit argument), is only
ever placed in the outbound ``Authorization`` header, and is never formatted
into an error message or log line.
"""

from __future__ import annotations

import json
import time

import httpx

from app.config import settings

# Retry budget: one initial request plus at most two retries.
MAX_RETRIES = 2
# Exponential backoff base in seconds; tests set this to 0 for zero delay.
RETRY_BACKOFF_SECONDS = 1.0


class LLMError(Exception):
    """Base class for all LLM client failures."""


class LLMConfigError(LLMError):
    """Configuration is invalid (e.g. no API key or rejected credentials)."""


class LLMTimeout(LLMError):
    """The request timed out even after retries."""


class LLMBadResponse(LLMError):
    """The response was an HTTP error or did not contain valid JSON."""


def _strip_json_fence(text: str) -> str:
    """Drop a leading ```json (or ```) line and a trailing ``` fence."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        newline = cleaned.find("\n")
        if newline == -1:
            return ""
        cleaned = cleaned[newline + 1 :]
    cleaned = cleaned.rstrip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _sleep_for(attempt: int) -> None:
    delay = RETRY_BACKOFF_SECONDS * (2**attempt)
    if delay > 0:
        time.sleep(delay)


class DeepSeekClient:
    """DeepSeek chat-completions client implementing :class:`LLMClient`.

    ``http_client`` is required: callers inject an ``httpx.Client`` in
    production and a fake in tests, so this client never opens a network
    connection on its own.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        http_client: object | None = None,
    ) -> None:
        self._api_key = api_key
        self.base_url = (
            base_url
            if base_url is not None
            else settings.deepseek_base_url
        ).rstrip("/")
        self.model = model if model is not None else settings.deepseek_model
        self._http_client = http_client

    def _resolve_key(self) -> str:
        if self._api_key is not None:
            return self._api_key
        return settings.deepseek_api_key

    def complete_json(
        self, system: str, user: str, *, temperature: float = 0.7
    ) -> dict:
        key = self._resolve_key()
        if not key:
            raise LLMConfigError("DeepSeek API key is not configured")
        if self._http_client is None:
            raise LLMConfigError("an HTTP client must be injected for LLM calls")

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        response = self._post_with_retries(url, body, headers)
        content = self._extract_content(response)
        cleaned = _strip_json_fence(content)
        try:
            data = json.loads(cleaned)
        except (ValueError, TypeError):
            raise LLMBadResponse("LLM response was not valid JSON") from None
        if not isinstance(data, dict):
            raise LLMBadResponse("LLM response JSON was not an object")
        return data

    def _post_with_retries(self, url: str, body: dict, headers: dict):
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._http_client.post(url, json=body, headers=headers)
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt >= MAX_RETRIES:
                    raise LLMTimeout("LLM request timed out") from None
                _sleep_for(attempt)
                continue

            status = response.status_code
            if status in (401, 403):
                raise LLMConfigError(
                    "LLM API rejected the configured credentials"
                )
            if status == 429 or status >= 500:
                if attempt >= MAX_RETRIES:
                    raise LLMError(
                        f"LLM request failed after retries (status {status})"
                    )
                _sleep_for(attempt)
                continue
            if status >= 400:
                raise LLMBadResponse(f"LLM request failed (status {status})")
            return response

        raise LLMError("LLM request failed") from last_error

    @staticmethod
    def _extract_content(response) -> str:
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMBadResponse("LLM response was missing message content") from None
        if not isinstance(content, str):
            raise LLMBadResponse("LLM response content was not text")
        return content
