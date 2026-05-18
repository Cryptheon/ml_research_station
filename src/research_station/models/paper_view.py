"""Paper view tracking — records which papers the user or agent accessed."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .paper import Base


class PaperViewORM(Base):
    __tablename__ = "paper_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    viewer: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "agent"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
