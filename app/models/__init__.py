"""ORM models. Import every module here so SQLAlchemy's metadata is complete."""

from app.models.aigc_audit import AigcAudit
from app.models.affiliate import (
    Agent,
    AgentFundEntry,
    Attribution,
    CommissionEntry,
    Invitation,
    Promoter,
    Withdrawal,
)
from app.models.audit import AuditLog
from app.models.catalog import Product
from app.models.credits import CreditBalance, CreditEntry
from app.models.generation import GenerationJob
from app.models.orders import Order, OrderEvent
from app.models.user import Role, User

ALL_MODELS = (
    User,
    Role,
    CreditEntry,
    CreditBalance,
    Product,
    Order,
    OrderEvent,
    GenerationJob,
    Invitation,
    Attribution,
    Promoter,
    Agent,
    AgentFundEntry,
    CommissionEntry,
    Withdrawal,
    AuditLog,
    AigcAudit,
)

# Tables that move money and therefore require a unique idempotency key.
FUND_MODELS = (
    CreditEntry,
    Order,
    OrderEvent,
    GenerationJob,
    AgentFundEntry,
    CommissionEntry,
    Withdrawal,
)

__all__ = [
    "User",
    "Role",
    "CreditEntry",
    "CreditBalance",
    "Product",
    "Order",
    "OrderEvent",
    "GenerationJob",
    "Invitation",
    "Attribution",
    "Promoter",
    "Agent",
    "AgentFundEntry",
    "CommissionEntry",
    "Withdrawal",
    "AuditLog",
    "AigcAudit",
    "ALL_MODELS",
    "FUND_MODELS",
]
