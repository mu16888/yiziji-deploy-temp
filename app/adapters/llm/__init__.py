"""LLM client contract used by the self-owned generation orchestration.

DeepSeek is just one replaceable implementation of :class:`LLMClient`; the
orchestration depends only on this protocol, never on a concrete vendor.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Complete a system/user prompt and return a parsed JSON object."""

    def complete_json(
        self, system: str, user: str, *, temperature: float = 0.7
    ) -> dict:
        ...
