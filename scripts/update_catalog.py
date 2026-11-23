"""Idempotent live-DB catalogue update for the 2026-11-23 pricing adjustment.

Adds the ¥9.9 entry SKU and retires the dead voice-clone SKU. Retirement is a
soft-delete: the row is kept and marked inactive, never hard-deleted. Agent
admission stays active in the DB and is hidden from the pricing page by
``app.services.catalog.HIDDEN_FROM_PRICING_SKUS`` instead.

Usage::

    python scripts/update_catalog.py
    python scripts/update_catalog.py --database-url sqlite:///./app.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Allow `python scripts/update_catalog.py` from the repo root without installing
# the package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.db import Base
from app.models import Product
from scripts.seed import PRODUCTS

STARTER_SKU = "pkg_starter_9"
VOICE_SKU = "svc_voice_clone_299"


def update_catalog(db: Session) -> list[str]:
    """Apply the adjustment to an existing catalogue; return action lines.

    Idempotent: a second call makes no further changes, it only reports that
    there is nothing left to do. The caller owns nothing — this commits.
    """
    actions: list[str] = []

    starter_data = next((dict(p) for p in PRODUCTS if p["sku"] == STARTER_SKU), None)
    if starter_data is None:
        raise RuntimeError(f"seed is missing {STARTER_SKU}; cannot migrate")

    if db.query(Product).filter_by(sku=STARTER_SKU).first() is None:
        db.add(Product(**starter_data))
        actions.append(
            f"added SKU {STARTER_SKU} "
            f"({starter_data['amount_fen']} fen / {starter_data['credits']} credits)"
        )
    else:
        actions.append(f"SKU {STARTER_SKU} already present (nothing to do)")

    voice = db.query(Product).filter_by(sku=VOICE_SKU).first()
    if voice is None:
        actions.append(f"retired SKU {VOICE_SKU} not present (nothing to do)")
    elif voice.active:
        voice.active = False
        actions.append(
            f"deactivated retired SKU {VOICE_SKU} (row kept, marked inactive)"
        )
    else:
        actions.append(f"SKU {VOICE_SKU} already inactive (nothing to do)")

    db.commit()
    return actions


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code (0 on success)."""
    parser = argparse.ArgumentParser(
        description="Apply the 2026-11-23 pricing adjustment to an existing DB"
    )
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="SQLAlchemy database URL (default: DATABASE_URL)",
    )
    args = parser.parse_args(argv)

    connect_args = (
        {"check_same_thread": False} if args.database_url.startswith("sqlite") else {}
    )
    engine = create_engine(args.database_url, connect_args=connect_args)
    try:
        Base.metadata.create_all(bind=engine)
        with sessionmaker(bind=engine)() as db:
            actions = update_catalog(db)
    finally:
        engine.dispose()

    for action in actions:
        print(action)
    print(f"Catalogue update complete ({len(actions)} step(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
