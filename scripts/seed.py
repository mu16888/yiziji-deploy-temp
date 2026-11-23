"""Idempotent seed data: default SKUs, roles and demo accounts.

Run after the migration::

    alembic upgrade head
    python scripts/seed.py
"""

import sys
from pathlib import Path

from sqlalchemy.orm import Session

# Allow `python scripts/seed.py` from the repo root without installing the package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Product, Role, User
from app.models.base import utcnow

DEFAULT_SKUS = frozenset(
    {
        "pkg_starter_9",
        "pkg_newcomer_experience_199",
        "pkg_personal_699",
        "pkg_team_1999",
        "pkg_enterprise_collab_3999",
        "agent_admission_9999",
    }
)

PRODUCTS = (
    {
        "sku": "pkg_starter_9",
        "name": "体验包",
        "type": "package",
        "amount_fen": 990,
        "original_amount_fen": None,
        "credits": 100,
        "bonuses": {},
        "eligibility": {},
        "sort": 0,
    },
    {
        "sku": "pkg_newcomer_experience_199",
        "name": "新人体验包",
        "type": "package",
        "amount_fen": 19900,
        "original_amount_fen": 19900,
        "credits": 1000,
        "bonuses": {"invite_bonus_credits": 50, "invite_cap": 20},
        "eligibility": {},
        "sort": 1,
    },
    {
        "sku": "pkg_personal_699",
        "name": "个人版",
        "type": "package",
        "amount_fen": 69900,
        "original_amount_fen": 69900,
        "credits": 6000,
        "bonuses": {"promoter_level": "basic"},
        "eligibility": {"repurchase_amount_fen": 55920},
        "sort": 2,
    },
    {
        "sku": "pkg_team_1999",
        "name": "进阶团队版",
        "type": "package",
        "amount_fen": 199900,
        "original_amount_fen": 199900,
        "credits": 18000,
        "bonuses": {"promoter_level": "advanced"},
        "eligibility": {"repurchase_amount_fen": 139930},
        "sort": 3,
    },
    {
        "sku": "pkg_enterprise_collab_3999",
        "name": "机构协作版",
        "type": "package",
        "amount_fen": 399900,
        "original_amount_fen": 399900,
        "credits": 45000,
        "bonuses": {"seats": 10, "org": True, "promoter_level": "advanced"},
        "eligibility": {},
        "sort": 4,
    },
    {
        "sku": "agent_admission_9999",
        "name": "正式代理准入",
        "type": "agent",
        "amount_fen": 999900,
        "original_amount_fen": None,
        "credits": 0,
        "bonuses": {"agent": True},
        "eligibility": {},
        "sort": 6,
    },
)

ROLES = (
    ("user", "普通用户（老师/学生）"),
    ("promoter", "推广员"),
    ("agent", "正式代理"),
    ("admin", "管理员"),
)

DEMO_USERS = (
    {
        "phone": "13900000001",
        "display_name": "演示老师",
        "role": "user",
        "invite_code": "TCH-DEMO1",
    },
    {
        "phone": "13900000002",
        "display_name": "演示机构（代理）",
        "role": "agent",
        "invite_code": "TCH-ORG01",
        "org_id": "ORG-DEMO01",
    },
    {
        "phone": "13900000003",
        "display_name": "演示管理员",
        "role": "admin",
        "invite_code": "TCH-ADMIN",
    },
)


def _get_or_create(db: Session, model, filters: dict, defaults: dict | None = None):
    obj = db.query(model).filter_by(**filters).first()
    if obj is None:
        # filters win on overlapping keys (e.g. sku / phone).
        obj = model(**{**(defaults or {}), **filters})
        db.add(obj)
    return obj


def seed(db: Session) -> None:
    """Insert default rows; safe to call repeatedly. Commits before returning."""
    for data in PRODUCTS:
        _get_or_create(db, Product, {"sku": data["sku"]}, data)

    for name, description in ROLES:
        _get_or_create(db, Role, {"name": name}, {"description": description})

    for data in DEMO_USERS:
        _get_or_create(
            db,
            User,
            {"phone": data["phone"]},
            {**data, "is_active": True, "created_at": utcnow()},
        )

    db.commit()


if __name__ == "__main__":
    from app.db import Base, SessionLocal, engine

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        seed(session)
    print(f"Seeded {len(PRODUCTS)} products, {len(ROLES)} roles, {len(DEMO_USERS)} demo users.")
