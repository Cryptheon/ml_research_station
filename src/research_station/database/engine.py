"""SQLAlchemy engine factory and session context manager."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..models.entity import (  # noqa: F401 — registers entity tables
    EntityRelationshipORM,
    PaperEntityORM,
)
from ..models.paper import Base, PaperEdgeORM  # noqa: F401 — registers paper_edges table
from ..models.paper_view import PaperViewORM  # noqa: F401 — registers paper_views table
from ..models.summary import PaperSummaryORM  # noqa: F401 — registers table with Base
from ..models.token_usage import TokenUsageORM  # noqa: F401 — registers token_usage table
from ..models.traversal import TraversalORM  # noqa: F401 — registers traversals table
from ..models.user import (  # noqa: F401 — registers user tables with Base
    ChatMessageORM,
    ChatORM,
    CollectionItemORM,
    CollectionORM,
    IngestHistoryORM,
    PaperNoteORM,
    PinORM,
    UserORM,
    WatchORM,
)
from ..models.web_link import WebPaperLinkORM  # noqa: F401 — registers web_paper_links table


def build_engine(db_path: Path) -> Engine:
    """Create a SQLite engine with WAL journal mode and FK enforcement.

    WAL mode allows concurrent readers alongside a single writer, which is
    important once the FastAPI backend serves the dashboard while a background
    ingestion job runs.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn: object, _: object) -> None:  # type: ignore[type-arg]
        cursor = dbapi_conn.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    Base.metadata.create_all(engine)

    # Incremental migrations for columns added after initial schema creation.
    _migrations = [
        "ALTER TABLE ingest_history ADD COLUMN paper_ids_json TEXT DEFAULT '[]'",
        "ALTER TABLE chat_messages ADD COLUMN images_json TEXT",
    ]
    with engine.connect() as conn:
        for stmt in _migrations:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # Column already exists

    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a configured session factory bound to *engine*."""
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def get_session(
    session_factory: sessionmaker[Session],
) -> Generator[Session, None, None]:
    """Unit-of-work context manager: commits on clean exit, rolls back on error."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
