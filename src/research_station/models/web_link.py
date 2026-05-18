"""WebPaperLinkORM — associates a web:<id> corpus entry with a requesting paper."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .paper import Base


class WebPaperLinkORM(Base):
    """Records that paper *paper_id* caused a web page to be ingested."""

    __tablename__ = "web_paper_links"

    web_paper_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    paper_id: Mapped[str] = mapped_column(String(200), primary_key=True, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
