"""FastAPI dependency injection helpers."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from ..config.settings import get_settings
from ..database.engine import build_engine, build_session_factory, get_session


def get_db() -> Generator[Session, None, None]:
    """Yield a database session; commit on success, rollback on error."""
    settings = get_settings()
    engine = build_engine(settings.database.sqlite_path)
    factory = build_session_factory(engine)
    with get_session(factory) as session:
        yield session
