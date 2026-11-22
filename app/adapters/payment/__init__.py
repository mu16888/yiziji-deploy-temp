"""Payment gateway contract, shared signature helpers and adapter selection.

Money is always integer 分. Adapters never touch the database: they create a
payable payload, verify a webhook signature, query a gateway and refund. The
service layer owns all state changes and credits.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from app.config import settings

PAYMENT_CHANNELS = frozenset({"jsapi", "native"})
_SIGNATURE_KEYS = ("signature", "sign")


class InvalidSignature(ValueError):
    """Raised when a callback signature does not match its payload."""


@runtime_checkable
class PaymentGateway(Protocol):
    """The four operations the order service relies on."""

    def create_payment(self, order) -> dict:
        """Return a channel-specific payload the client can pay with."""
        ...

    def verify_callback(self, payload: dict, signature: str) -> str:
        """Verify the signature and return the callback's ``order_no``."""
        ...

    def query(self, order_no: str) -> str:
        """Return the gateway-side status string for an order."""
        ...

    def refund(self, order_no: str, amount_fen: int) -> bool:
        """Refund ``amount_fen``; return True when the gateway accepted it."""
        ...


def signable_payload(payload: dict) -> dict:
    """Strip transport-only fields so signed bytes are reproducible."""
    return {k: v for k, v in payload.items() if k not in _SIGNATURE_KEYS}


def canonical_json(payload: dict) -> str:
    """Stable JSON used by both signing and verification."""
    return json.dumps(
        signable_payload(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def get_gateway(name: str | None = None) -> PaymentGateway:
    """Resolve the configured gateway (``mock`` by default)."""
    which = (name or settings.payment_gateway or "mock").lower()
    if which == "wechat":
        from app.adapters.payment import wechat as adapter

        return adapter.WeChatGateway()
    if which == "mock":
        from app.adapters.payment import mock as adapter

        return adapter.MockGateway()
    raise ValueError(f"unknown payment gateway: {which}")
