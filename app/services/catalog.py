"""Product catalogue, server-side pricing and post-purchase entitlements.

Every price is read from a :class:`Product` row. ``get_price`` is the single
source of truth that order creation (T6) and the /pricing page both use; the
client never sends a price. No FastAPI imports live in this module.
"""

from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import Agent, AuditLog, Order, Product, Promoter, User

# A historical, not-refunded successful paid order for the SAME sku qualifies
# the unified subject for that sku's repurchase price (spec §5.6).
_PAID_STATUSES = frozenset({"paid", "fulfilled"})
_REFUND_STATUSES = frozenset({"refunding", "refunded"})

# Policy flags surfaced to the client. The newcomer package locks attribution
# but pays no cash commission (the commission engine itself is Task 8).
_NO_CASH_COMMISSION_SKUS = frozenset({"pkg_newcomer_experience_199"})
_ATTRIBUTION_LOCKING_TYPES = frozenset({"package"})

_GRANT_ACTION = "bonus:grant"

# Entitlement upgrades are monotonic: buying a lower tier must not downgrade a
# promoter who already earned a higher level.
_PROMOTER_LEVEL_RANK = {"basic": 0, "advanced": 1}


class ProductNotFound(LookupError):
    """Raised when a SKU is absent from the catalogue."""


@dataclass(frozen=True)
class PriceQuote:
    """The only authoritative answer to "what does this SKU cost this user?"."""

    amount_fen: int
    credits: int
    original_amount_fen: int | None
    eligible: bool

    @property
    def amount_yuan(self) -> str:
        """Presentation-only formatting; money is stored and moved in fen."""
        return f"{self.amount_fen / 100:.2f}"


@dataclass(frozen=True)
class ProductView:
    sku: str
    name: str
    type: str
    description: str | None
    amount_fen: int
    credits: int
    original_amount_fen: int | None
    eligible: bool
    repurchase: bool
    cash_commission: bool
    locks_attribution: bool

    @property
    def amount_yuan(self) -> str:
        return f"{self.amount_fen / 100:.2f}"

    @property
    def original_amount_yuan(self) -> str:
        if self.original_amount_fen is None:
            return ""
        return f"{self.original_amount_fen / 100:.2f}"


def _repurchase_amount_fen(product: Product) -> int | None:
    rules = product.eligibility or {}
    value = rules.get("repurchase_amount_fen")
    return int(value) if value is not None else None


def _has_paid_order_for_sku(db: Session, user: User, sku: str) -> bool:
    """True if the unified subject has a successful, non-refunded order for sku.

    "Unified subject" means the same user id, or (when present) a shared
    ``identity_hash`` or ``org_id``, so several accounts of one real person or
    organisation share the repurchase entitlement.
    """
    subject = []
    if getattr(user, "id", None) is not None:
        subject.append(Order.user_id == user.id)
    identity_hash = getattr(user, "identity_hash", None)
    if identity_hash:
        subject.append(User.identity_hash == identity_hash)
    org_id = getattr(user, "org_id", None)
    if org_id:
        subject.append(User.org_id == org_id)
    if not subject:
        return False

    query = (
        db.query(Order.id)
        .join(User, User.id == Order.user_id)
        .filter(Order.sku == sku)
        .filter(Order.status.in_(_PAID_STATUSES))
        .filter(
            or_(
                Order.refund_status.is_(None),
                ~Order.refund_status.in_(_REFUND_STATUSES),
            )
        )
        .filter(or_(*subject))
    )
    return query.first() is not None


def _quote(db: Session, product: Product, user: User) -> tuple[PriceQuote, bool]:
    repurchase_amount = _repurchase_amount_fen(product)
    is_repurchase = repurchase_amount is not None and _has_paid_order_for_sku(
        db, user, product.sku
    )
    amount = repurchase_amount if is_repurchase else int(product.amount_fen)
    quote = PriceQuote(
        amount_fen=amount,
        credits=int(product.credits),
        original_amount_fen=product.original_amount_fen,
        eligible=bool(product.active),
    )
    return quote, is_repurchase


def get_price(db: Session, sku: str, user: User) -> PriceQuote:
    """Return the one legal price for ``sku`` given the caller's state."""
    product = db.query(Product).filter_by(sku=sku).first()
    if product is None:
        raise ProductNotFound(f"unknown sku: {sku}")
    return _quote(db, product, user)[0]


def list_products(db: Session, user: User) -> list[ProductView]:
    """Active catalogue in display order, with prices computed server-side."""
    products = (
        db.query(Product)
        .filter(Product.active.is_(True))
        .order_by(Product.sort, Product.id)
        .all()
    )
    views: list[ProductView] = []
    for product in products:
        quote, is_repurchase = _quote(db, product, user)
        views.append(
            ProductView(
                sku=product.sku,
                name=product.name,
                type=product.type,
                description=product.description,
                amount_fen=quote.amount_fen,
                credits=quote.credits,
                original_amount_fen=quote.original_amount_fen,
                eligible=quote.eligible,
                repurchase=is_repurchase,
                cash_commission=product.sku not in _NO_CASH_COMMISSION_SKUS,
                locks_attribution=product.type in _ATTRIBUTION_LOCKING_TYPES,
            )
        )
    return views


def _record_grant(db: Session, user_id: int, sku: str, bonuses: dict) -> None:
    """Store an auditable, retry-safe record for entitlements without a table."""
    prior = (
        db.query(AuditLog)
        .filter_by(action=_GRANT_ACTION, target_type="user", target_id=str(user_id))
        .all()
    )
    if any((row.after or {}).get("sku") == sku for row in prior):
        return
    db.add(
        AuditLog(
            actor_id=None,
            action=_GRANT_ACTION,
            target_type="user",
            target_id=str(user_id),
            after={"sku": sku, **bonuses},
            reason=f"entitlements from {sku}",
        )
    )


def apply_bonuses(db: Session, user_id: int, sku: str) -> None:
    """Grant a product's non-credit entitlements; idempotent, caller commits.

    Promoter level and agency are written to the first-class rows Task 8/9
    consume. Entitlements without a dedicated table (courses, voice timbres,
    org seats, invite policy) are recorded as an audit-style grant record.
    """
    product = db.query(Product).filter_by(sku=sku).first()
    if product is None:
        return
    bonuses = dict(product.bonuses or {})
    if not bonuses:
        return

    if "promoter_level" in bonuses:
        new_level = bonuses["promoter_level"]
        promoter = db.query(Promoter).filter_by(user_id=user_id).first()
        if promoter is None:
            db.add(Promoter(user_id=user_id, level=new_level))
        elif _PROMOTER_LEVEL_RANK.get(new_level, 0) > _PROMOTER_LEVEL_RANK.get(
            promoter.level, 0
        ):
            promoter.level = new_level
        user = db.get(User, user_id)
        if user is not None and user.role == "user":
            user.role = "promoter"

    if bonuses.get("agent"):
        if db.query(Agent).filter_by(user_id=user_id).first() is None:
            db.add(Agent(user_id=user_id, status="pending"))

    if bonuses.get("org"):
        user = db.get(User, user_id)
        if user is not None and not user.org_id:
            user.org_id = f"ORG-U{user_id}"

    _record_grant(db, user_id, sku, bonuses)
    db.flush()
