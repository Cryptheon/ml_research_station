"""Token usage ORM model — persists per-call LLM cost data."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .paper import Base


def _utcnow() -> datetime:
    return datetime.utcnow()


class TokenUsageORM(Base):
    __tablename__ = "token_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    endpoint: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # "chat" | "agent" | "summarize" | "ocr" | "embed"
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_time_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
