"""SQLAlchemy engine, session factory, declarative base and FastAPI DB dependency."""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

# SQLite needs check_same_thread disabled so the session factory can be used
# from FastAPI's worker threads; other drivers must not receive that argument.
_connect_args = (
    {"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {}
)

engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Yield a DB session and always close it, for use as a FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
