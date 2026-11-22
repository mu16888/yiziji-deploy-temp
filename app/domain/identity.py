"""Pure identity/role rules. No FastAPI, no Request, no I/O."""

from typing import Protocol, runtime_checkable

# Role seniority, low -> high. The controller ruling is:
#   admin >= agent >= promoter >= user
# i.e. an "agent" satisfies require_roles("promoter") and require_roles("user"),
# and "admin" satisfies everything. Never the other way around.
ROLE_RANK: dict[str, int] = {
    "user": 0,
    "promoter": 1,
    "agent": 2,
    "admin": 3,
}


def role_satisfies(role: str, required: str) -> bool:
    """True when ``role`` is at least as senior as ``required``.

    Unknown roles (or unknown requirements) never grant access.
    """
    have = ROLE_RANK.get(role)
    need = ROLE_RANK.get(required)
    if have is None or need is None:
        return False
    return have >= need


@runtime_checkable
class IdentityProvider(Protocol):
    """Pluggable identity adapter contract (implemented in app/adapters/identity)."""

    def authenticate(self, db, credential: str, *, subject: str | None = None): ...
