"""SQLAlchemy engine/session and the get_db FastAPI dependency."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# Neon drops idle serverless connections; a pooled one reused across warm
# invocations can therefore be dead by the next request. pool_pre_ping checks a
# connection is alive before handing it out (and transparently reconnects if
# not), and pool_recycle proactively discards connections older than 5 minutes —
# comfortably under Neon's idle window — so a stale socket never reaches a query
# and surfaces as an unhandled 500. (For connection-limit issues, prefer Neon's
# pooled connection string — the `-pooler` host — via DATABASE_URL.)
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=300,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
