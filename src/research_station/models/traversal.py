"""Persistent graph traversal results."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .paper import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TraversalORM(Base):
    __tablename__ = "traversals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    start_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    start_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Full traversal result JSON (nodes_visited, edges_walked, params, stopped_reason, etc.)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
