"""WeChat Pay adapter (template hook): signature check + JSAPI/Native orders.

The signing follows WeChat Pay v3's ``timestamp\\nnonce\\nbody\\n`` HMAC-SHA256
shape. Credentials default to fake values so local runs and tests need no real
merchant account; a deployment overrides them through the environment. No HTTP
call is made — order creation and refunds are deterministic template stubs.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app.adapters.payment import (
    PAYMENT_CHANNELS,
    InvalidSignature,
    canonical_json,
    signable_payload,
)
from app.config import settings

_PENDING = "pending_payment"


def _api_v3_key() -> str:
    return getattr(settings, "wechat_api_v3_key", "") or "fake-api-v3-key"


def sign(payload: dict) -> str:
    """Compute a WeChat-v3-style signature for ``payload``."""
    signed = signable_payload(payload)
    timestamp = str(signed.get("timestamp", ""))
    nonce = str(signed.get("nonce", ""))
    body = canonical_json(signed)
    message = f"{timestamp}\n{nonce}\n{body}\n"
    return hmac.new(_api_v3_key().encode(), message.encode(), hashlib.sha256).hexdigest()


def _prepay_id(order_no: str) -> str:
    return "wx" + hashlib.sha256(order_no.encode()).hexdigest()[:20]


class WeChatGateway:
    def create_payment(self, order) -> dict:
        channel = getattr(order, "channel", None)
        if channel not in PAYMENT_CHANNELS:
            raise ValueError(f"unsupported payment channel: {channel}")
        order_no = str(order.no)
        amount = int(order.amount_fen)
        app_id = getattr(settings, "wechat_app_id", "") or "wx-fake-app-id"
        mch_id = getattr(settings, "wechat_mch_id", "") or "fake-mch-id"
        prepay = _prepay_id(order_no)

        if channel == "native":
            return {
                "channel": "native",
                "order_no": order_no,
                "amount_fen": amount,
                "appid": app_id,
                "mch_id": mch_id,
                "prepay_id": prepay,
                "code_url": f"weixin://wxpay/bizpayurl?pr={prepay}",
            }

        params = {
            "appId": app_id,
            "timeStamp": "1700000000",
            "nonceStr": secrets.token_hex(8),
            "package": f"prepay_id={prepay}",
            "signType": "RSA",
        }
        return {
            "channel": "jsapi",
            "order_no": order_no,
            "amount_fen": amount,
            **params,
            "paySign": sign({**params, "timestamp": params["timeStamp"], "nonce": params["nonceStr"]}),
        }

    def verify_callback(self, payload: dict, signature: str) -> str:
        expected = sign(payload)
        if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
            raise InvalidSignature("invalid WeChat callback signature")
        order_no = payload.get("order_no")
        if not order_no:
            raise InvalidSignature("callback is missing order_no")
        return str(order_no)

    def query(self, order_no: str) -> str:
        return _PENDING

    def refund(self, order_no: str, amount_fen: int) -> bool:
        if isinstance(amount_fen, bool) or not isinstance(amount_fen, int):
            raise ValueError("refund amount must be an integer in fen")
        if amount_fen < 0:
            raise ValueError("refund amount must be non-negative")
        return True
