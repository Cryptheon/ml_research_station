"""Entity and typed-relationship models for structured knowledge extraction.

Entities are named things extracted from paper content by the LLM:
  person | project | library | concept | dataset | method | organization | file | decision

EntityRelationships connect two entities with a typed, directed edge:
  created_by | maintained_by | depends_on | uses | extends | contradicts |
  caused | fixed | supersedes | part_of | evaluated_on | introduced_in |
  applied_to | owned_by | related_to
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .paper import Base

ENTITY_TYPES = {
    "person",
    "project",
    "library",
    "concept",
    "dataset",
    "method",
    "organization",
    "file",
    "decision",
}

RELATIONSHIP_TYPES = {
    "created_by",  # entity created/authored by person or org
    "maintained_by",  # entity actively maintained by
    "depends_on",  # hard technical dependency
    "uses",  # A employs B as a tool/component
    "extends",  # A inherits from or builds on B
    "contradicts",  # A's findings contradict B
    "caused",  # A directly caused/triggered B
    "fixed",  # A resolved/addressed B (bug, limitation, issue)
    "supersedes",  # A replaces B
    "part_of",  # A is a component of B
    "evaluated_on",  # method A evaluated on dataset/benchmark B
    "introduced_in",  # concept A was first proposed in paper B
    "applied_to",  # method A applied to domain/task B
    "owned_by",  # project/decision owned by person or org
    "related_to",  # weak general connection (catch-all)
}


# ── Pydantic schemas ──────────────────────────────────────────────────────────


class ExtractedEntity(BaseModel):
    name: str
    entity_type: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class ExtractedRelationship(BaseModel):
    from_entity: str
    to_entity: str
    relationship_type: str
    description: str = ""
    confidence: float = 0.8


class EntityExtractionResult(BaseModel):
    paper_id: str
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    model_used: str = ""
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ── ORM models ────────────────────────────────────────────────────────────────


class PaperEntityORM(Base):
    """A named entity extracted from a paper by the LLM."""

    __tablename__ = "paper_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    attributes_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    model_used: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def attributes(self) -> dict[str, Any]:
        return json.loads(self.attributes_json)


class EntityRelationshipORM(Base):
    """A typed, directed relationship between two extracted entities."""

    __tablename__ = "entity_relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("paper_entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("paper_entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relationship_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)
    source_paper_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
