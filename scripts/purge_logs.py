"""Scheduled cleanup: purge AIGC audit logs older than AUDIT_RETENTION_DAYS.

Usage::

    python scripts/purge_logs.py
    python scripts/purge_logs.py --database-url sqlite:///./app.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Allow `python scripts/purge_logs.py` from the repo root without installing the package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.services.compliance import purge_expired


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code (0 on success)."""
    parser = argparse.ArgumentParser(description="Purge expired AIGC audit logs")
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
        with sessionmaker(bind=engine)() as db:
            removed = purge_expired(db)
    finally:
        engine.dispose()

    print(
        f"Purged {removed} AIGC audit log(s) older than "
        f"{settings.audit_retention_days} days."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
