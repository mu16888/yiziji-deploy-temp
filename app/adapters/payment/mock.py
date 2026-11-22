"""Zero-dependency mock payment gateway for local runs and tests.

Signatures are HMAC-SHA256 over the canonical signed payload; ``sign()`` is
exported so tests can produce valid callbacks without any external service.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.adapters.payment import (
    PAYMENT_CHANNELS,
    InvalidSignature,
    canonical_json,
)

_MOCK_SECRET = "mock-pay-secret"
_PENDING = "pending_payment"
_PAID = "paid"


def sign(payload: dict) -> str:
    """Return the mock signature for ``payload`` (transport keys ignored)."""
    return hmac.new(
        _MOCK_SECRET.encode(), canonical_json(payload).encode(), hashlib.sha256
    ).hexdigest()


class MockGateway:
    """In-memory gateway; status registry is shared across instances."""

    _statuses: dict[str, str] = {}

    def create_payment(self, order) -> dict:
        channel = getattr(order, "channel", None)
        if channel not in PAYMENT_CHANNELS:
            raise ValueError(f"unsupported payment channel: {channel}")
        order_no = str(order.no)
        amount = int(order.amount_fen)
        if channel == "native":
            code = f"mock://pay/native/{order_no}"
            return {
                "channel": "native",
                "order_no": order_no,
                "amount_fen": amount,
                "code_url": code,
                "qr_content": code,
            }

        package = f"prepay_id=mock{hashlib.sha256(order_no.encode()).hexdigest()[:16]}"
        params = {
            "appId": "mock-app-id",
            "timeStamp": "1700000000",
            "nonceStr": secrets.token_hex(8),
            "package": package,
            "signType": "RSA",
        }
        return {
            "channel": "jsapi",
            "order_no": order_no,
            "amount_fen": amount,
            **params,
            "paySign": sign(params),
        }

    def verify_callback(self, payload: dict, signature: str) -> str:
        expected = sign(payload)
        if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
            raise InvalidSignature("invalid mock callback signature")
        order_no = payload.get("order_no")
        if not order_no:
            raise InvalidSignature("callback is missing order_no")
        return str(order_no)

    def query(self, order_no: str) -> str:
        return self._statuses.get(str(order_no), _PENDING)

    def refund(self, order_no: str, amount_fen: int) -> bool:
        if isinstance(amount_fen, bool) or not isinstance(amount_fen, int):
            raise ValueError("refund amount must be an integer in fen")
        if amount_fen < 0:
            raise ValueError("refund amount must be non-negative")
        self._statuses[str(order_no)] = "refunded"
        return True

    def mark_paid(self, order_no: str) -> None:
        """Test helper: simulate the gateway having received a payment."""
        self._statuses[str(order_no)] = _PAID
