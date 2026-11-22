"""WeChat OAuth identity adapter — template hook, intentionally unimplemented.

A fork wires this to its own WeChat app id/secret and code->openid exchange,
using the config values documented in the design spec.
"""


def verify_code(code: str) -> bool:  # pragma: no cover - not used in dev
    return False


def authenticate(db, credential: str, *, subject: str | None = None):
    raise NotImplementedError("wechat identity adapter is a deployment hook")
