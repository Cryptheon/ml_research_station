"""Paper summary schemas and ORM model.

``PaperSummary`` is the Pydantic schema used throughout the processing layer.
``PaperSummaryORM`` persists summaries to SQLite alongside the ``papers`` table.
Multiple summaries per paper are allowed (one per model/run), enabling
comparison between providers or re-generation after a model upgrade.
"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .paper import Base


class PaperSummary(BaseModel):
    """Structured LLM-generated analysis of a single paper."""

    # Linkage
    paper_id: str
    model_used: str
    provider: str

    # Core content — all produced by the LLM in a single structured call
    tldr: str = Field(description="One or two sentence plain-language summary")
    contributions: list[str] = Field(description="Main claimed contributions")
    methodology: str = Field(description="How the approach works")
    key_results: list[str] = Field(description="Concrete empirical or theoretical results")
    limitations: list[str] = Field(description="Weaknesses or caveats the authors acknowledge")
    related_work_context: str = Field(
        description="Where this fits in the broader research landscape"
    )
    interesting_aspects: list[str] = Field(description="What makes this paper worth reading")
    suggested_follow_up: list[str] = Field(
        description="Related papers or research directions to explore"
    )

    # Reasoning trace — raw thinking output from the model (may be empty)
    thinking_trace: str = ""
    prompt_used: str = ""

    # Metadata
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    generation_time_seconds: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class PaperSummaryORM(Base):
    """SQLAlchemy mapping for the ``paper_summaries`` table."""

    __tablename__ = "paper_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    model_used: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    tldr: Mapped[str] = mapped_column(Text, nullable=False)
    contributions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    methodology: Mapped[str] = mapped_column(Text, nullable=False)
    key_results_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    limitations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    related_work_context: Mapped[str] = mapped_column(Text, nullable=False)
    interesting_aspects_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    suggested_follow_up_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    thinking_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_used: Mapped[str] = mapped_column(Text, nullable=False)
    generation_time_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def to_pydantic(self) -> PaperSummary:
        return PaperSummary(
            paper_id=self.paper_id,
            model_used=self.model_used,
            provider=self.provider,
            tldr=self.tldr,
            contributions=json.loads(self.contributions_json),
            methodology=self.methodology,
            key_results=json.loads(self.key_results_json),
            limitations=json.loads(self.limitations_json),
            related_work_context=self.related_work_context,
            interesting_aspects=json.loads(self.interesting_aspects_json),
            suggested_follow_up=json.loads(self.suggested_follow_up_json),
            thinking_trace=self.thinking_trace or "",
            prompt_used=self.prompt_used,
            generated_at=self.generated_at,
            generation_time_seconds=self.generation_time_seconds,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )

    @classmethod
    def from_pydantic(cls, summary: PaperSummary) -> PaperSummaryORM:
        return cls(
            paper_id=summary.paper_id,
            model_used=summary.model_used,
            provider=summary.provider,
            tldr=summary.tldr,
            contributions_json=json.dumps(summary.contributions),
            methodology=summary.methodology,
            key_results_json=json.dumps(summary.key_results),
            limitations_json=json.dumps(summary.limitations),
            related_work_context=summary.related_work_context,
            interesting_aspects_json=json.dumps(summary.interesting_aspects),
            suggested_follow_up_json=json.dumps(summary.suggested_follow_up),
            thinking_trace=summary.thinking_trace or None,
            prompt_used=summary.prompt_used,
            generation_time_seconds=summary.generation_time_seconds,
            prompt_tokens=summary.prompt_tokens,
            completion_tokens=summary.completion_tokens,
            generated_at=summary.generated_at,
        )
