"""Pure commission policy and the §5.6 one-level team-difference formula.

Every rate is an integer percent; money is computed in 分 with integer floor
division so no floating point ever touches a ledger. This module has no DB,
SQLAlchemy or FastAPI imports.
"""

from dataclasses import dataclass

# Channel cap: promoter + agent may never exceed 40% of the customer's final
# legal paid amount. The promoter's level rate is always below the cap, so the
# agent simply receives the remainder (or the full 40% when no promoter exists).
CHANNEL_CAP_PERCENT = 40

# level -> {first order, repurchase}, in integer percent.
PROMOTER_RATES: dict[str, dict[str, int]] = {
    "basic": {"first": 20, "repurchase": 10},
    "advanced": {"first": 30, "repurchase": 15},
}

# The ¥199 newcomer package locks attribution but pays no cash commission; it
# grants the inviter 50 credits instead (capped at 20 invited people).
NO_CASH_COMMISSION_SKUS = frozenset({"pkg_newcomer_experience_199"})
INVITE_BONUS_CREDITS = 50
INVITE_BONUS_CAP = 20

# Only package-type orders lock attribution.
ATTRIBUTION_LOCKING_TYPES = frozenset({"package"})

# Agency (代开) orders are paid from the non-withdrawable agent fund. They are
# marked with a dedicated channel and never produce promoter/agent commission.
AGENCY_CHANNEL = "agent_fund"
AGENCY_FUND_DEBIT_PERCENT = 60  # platform deducts 60% of the official price

ADMISSION_SKU = "agent_admission_9999"

ACTIVE_ORDER_STATUSES = frozenset({"paid", "fulfilled"})
REFUND_STATUSES = frozenset({"refunding", "refunded"})


class InsufficientAgentFund(Exception):
    """Raised when an agency (代开) order would drive the agent fund negative."""


@dataclass(frozen=True)
class CommissionSplit:
    """Result of the §5.6 formula, all in 分."""

    promoter_fen: int
    agent_fen: int

    @property
    def total_fen(self) -> int:
        return self.promoter_fen + self.agent_fen


def promoter_rate_percent(level: str | None, is_first_order: bool) -> int:
    """Level rate for a first order or a repurchase (unknown level -> basic)."""
    rates = PROMOTER_RATES.get(level or "basic", PROMOTER_RATES["basic"])
    return rates["first" if is_first_order else "repurchase"]


def compute_commission(
    base_fen: int,
    *,
    promoter_level: str | None,
    is_first_order: bool,
    has_promoter: bool,
    has_agent: bool,
) -> CommissionSplit:
    """Apply the 40% channel cap and the one-level team difference (§5.6).

    The promoter takes its level rate first; the agent receives
    ``paid × 40% − promoter_commission`` (or the full 40% without a promoter).
    With only a promoter and no agent, nobody receives the unused cap headroom.
    """
    base = max(0, int(base_fen))
    cap = base * CHANNEL_CAP_PERCENT // 100
    promoter = 0
    if has_promoter:
        promoter = base * promoter_rate_percent(promoter_level, is_first_order) // 100
    agent = max(0, cap - promoter) if has_agent else 0
    return CommissionSplit(promoter_fen=promoter, agent_fen=agent)


def is_self_referral(customer, inviter, check: str) -> bool:
    """Anti-arbitrage gate over user-like objects (id/identity_hash/org_id/role).

    Using one's own invite code, and agents buying from each other, are always
    rejected. ``identity`` / ``org`` / ``both`` additionally gate the optional
    hashed-identity and organisation signals (``off`` by default in dev).
    """
    if customer is None or inviter is None:
        return False
    customer_id = getattr(customer, "id", None)
    inviter_id = getattr(inviter, "id", None)
    if customer_id is not None and customer_id == inviter_id:
        return True

    normalized = (check or "off").lower()
    if normalized in ("identity", "both"):
        customer_hash = getattr(customer, "identity_hash", None)
        inviter_hash = getattr(inviter, "identity_hash", None)
        if customer_hash and inviter_hash and customer_hash == inviter_hash:
            return True
    if normalized in ("org", "both"):
        customer_org = getattr(customer, "org_id", None)
        inviter_org = getattr(inviter, "org_id", None)
        if customer_org and inviter_org and customer_org == inviter_org:
            return True

    # Agents buying from each other is self-dealing regardless of switches.
    if (
        getattr(customer, "role", None) == "agent"
        and getattr(inviter, "role", None) == "agent"
    ):
        return True
    return False


def is_agency_order(order) -> bool:
    """True when the order is a fund-funded 代开 order."""
    return (
        getattr(order, "channel", None) == AGENCY_CHANNEL
        or getattr(order, "is_agency", False) is True
    )


def is_valid_order(order) -> bool:
    """A paid, non-refunded order is '有效订单' for attribution and commission."""
    if getattr(order, "paid_at", None) is None:
        return False
    if getattr(order, "paid_amount_fen", None) is None:
        return False
    if getattr(order, "status", None) in REFUND_STATUSES:
        return False
    if getattr(order, "refund_status", None) in REFUND_STATUSES:
        return False
    return True
