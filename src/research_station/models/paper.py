"""Paper domain models.

Pydantic schemas are used for data validation, inter-module transfer, and
API serialisation.  SQLAlchemy ORM classes map to SQLite tables.
Conversion helpers (to_pydantic / from_pydantic) keep the two in sync without
coupling the rest of the codebase to ORM internals.

cache_flags is a bitfield integer:
    bit 0 (1)  → PDF downloaded
    bit 1 (2)  → embeddings computed
    bit 2 (4)  → summary generated
    bit 3 (8)  → figures extracted
    bit 4 (16) → references resolved
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ── Enums ────────────────────────────────────────────────────────────────────


class PaperSource(str, Enum):
    """Canonical source identifiers used as namespace prefixes in paper IDs."""

    ARXIV = "arxiv"
    BIORXIV = "biorxiv"
    OPENREVIEW = "openreview"
    PUBMED = "pubmed"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    WIKIPEDIA = "wikipedia"
    WEB = "web"

    def __str__(self) -> str:
        return self.value


class PaperStatus(str, Enum):
    """Processing state of a paper in the local corpus."""

    QUEUED = "queued"
    SUMMARIZED = "summarized"
    PAYWALLED = "paywalled"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


# cache_flags bit positions
CACHE_PDF = 1
CACHE_EMBEDDINGS = 2
CACHE_SUMMARY = 4
CACHE_FIGURES = 8
CACHE_REFERENCES = 16
CACHE_FULLTEXT = 32  # full text extracted (OCR or native PDF text layer)


# ── Pydantic schemas ─────────────────────────────────────────────────────────


class Author(BaseModel):
    """Paper author with optional affiliation data."""

    name: str
    affiliations: list[str] = []
    email: str | None = None
    semantic_scholar_id: str | None = None
    orcid: str | None = None


class PaperScores(BaseModel):
    """Computed quality/relevance scores, all in [0, 1]."""

    relevance: float = 0.0
    novelty: float = 0.0
    velocity: float = 0.0


class Paper(BaseModel):
    """Canonical, source-agnostic paper representation.

    The ``id`` field uses a namespaced format so papers from different sources
    can be stored together without collision:
        arXiv paper  → ``arxiv:2301.00001``
        DOI paper    → ``doi:10.1101/2024.01.01.000001``
        OpenReview   → ``openreview:abc123``
    """

    id: str = Field(description="Canonical namespaced ID, e.g. 'arxiv:2301.00001'")
    title: str
    abstract: str | None = None
    authors: list[Author] = []
    categories: list[str] = []
    keywords: list[str] = []
    source: PaperSource
    venue: str | None = None
    published_date: datetime
    updated_date: datetime
    pdf_url: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    semantic_scholar_id: str | None = None
    openreview_id: str | None = None
    citation_count: int | None = None
    reference_count: int | None = None
    influential_citation_count: int | None = None
    tldr: str | None = None
    local_pdf_path: Path | None = None
    is_downloaded: bool = False
    raw_metadata: dict[str, Any] = {}

    # ── Dashboard fields ──────────────────────────────────────────────────

    topics: list[str] = Field(default=[], description="Fixed-vocab taxonomy labels")
    pinned: bool = False
    status: PaperStatus = PaperStatus.QUEUED
    scores: PaperScores = Field(default_factory=PaperScores)
    velocity_12w: list[int] = Field(
        default=[],
        description="Weekly citation deltas for the last 12 weeks (oldest→newest)",
    )
    cache_flags: int = Field(
        default=0,
        description="Bitfield: 1=pdf 2=embeddings 4=summary 8=figures 16=references",
    )
    created_at: datetime | None = None

    # ── ID factory helpers ────────────────────────────────────────────────

    @classmethod
    def make_arxiv_id(cls, arxiv_id: str) -> str:
        return f"arxiv:{arxiv_id}"

    @classmethod
    def make_doi_id(cls, doi: str) -> str:
        return f"doi:{doi.lower()}"

    @classmethod
    def make_openreview_id(cls, forum_id: str) -> str:
        return f"openreview:{forum_id}"

    @classmethod
    def make_s2_id(cls, s2_id: str) -> str:
        return f"s2:{s2_id}"


class Citation(BaseModel):
    """Directed citation edge: ``citing_paper_id`` → ``cited_paper_id``."""

    citing_paper_id: str
    cited_paper_id: str
    context: str | None = None
    is_influential: bool = False


# ── SQLAlchemy ORM ────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    pass


class PaperORM(Base):
    """SQLAlchemy mapping for the ``papers`` table.

    JSON columns (authors, categories, keywords, raw_metadata, topics,
    velocity_12w) are stored as TEXT and serialised/deserialised explicitly to
    keep the schema portable across SQLite versions without extensions.
    """

    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    authors_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    categories_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    keywords_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    venue: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    published_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    updated_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(50), nullable=True, unique=True)
    semantic_scholar_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    openreview_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    citation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    influential_citation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tldr: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_downloaded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # ── Dashboard columns ─────────────────────────────────────────────────

    topics_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PaperStatus.QUEUED.value, index=True
    )
    score_relevance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score_novelty: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score_velocity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    velocity_12w_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    cache_flags: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def to_pydantic(self) -> Paper:
        """Deserialise ORM row into a validated Paper model."""
        raw_authors: list[dict[str, Any]] = json.loads(self.authors_json)
        return Paper(
            id=self.id,
            title=self.title,
            abstract=self.abstract,
            authors=[Author(**a) for a in raw_authors],
            categories=json.loads(self.categories_json),
            keywords=json.loads(self.keywords_json),
            source=PaperSource(self.source),
            venue=self.venue,
            published_date=self.published_date,
            updated_date=self.updated_date,
            pdf_url=self.pdf_url,
            doi=self.doi,
            arxiv_id=self.arxiv_id,
            semantic_scholar_id=self.semantic_scholar_id,
            openreview_id=self.openreview_id,
            citation_count=self.citation_count,
            reference_count=self.reference_count,
            influential_citation_count=self.influential_citation_count,
            tldr=self.tldr,
            local_pdf_path=Path(self.local_pdf_path) if self.local_pdf_path else None,
            is_downloaded=self.is_downloaded,
            raw_metadata=json.loads(self.raw_metadata_json),
            topics=json.loads(self.topics_json),
            pinned=self.pinned,
            status=PaperStatus(self.status),
            scores=PaperScores(
                relevance=self.score_relevance,
                novelty=self.score_novelty,
                velocity=self.score_velocity,
            ),
            velocity_12w=json.loads(self.velocity_12w_json),
            cache_flags=self.cache_flags,
            created_at=self.created_at,
        )

    @classmethod
    def from_pydantic(cls, paper: Paper) -> PaperORM:
        """Serialise a Paper model into an ORM row (no session attachment)."""
        return cls(
            id=paper.id,
            title=paper.title,
            abstract=paper.abstract,
            authors_json=json.dumps([a.model_dump() for a in paper.authors]),
            categories_json=json.dumps(paper.categories),
            keywords_json=json.dumps(paper.keywords),
            source=paper.source.value,
            venue=paper.venue,
            published_date=paper.published_date,
            updated_date=paper.updated_date,
            pdf_url=paper.pdf_url,
            doi=paper.doi,
            arxiv_id=paper.arxiv_id,
            semantic_scholar_id=paper.semantic_scholar_id,
            openreview_id=paper.openreview_id,
            citation_count=paper.citation_count,
            reference_count=paper.reference_count,
            influential_citation_count=paper.influential_citation_count,
            tldr=paper.tldr,
            local_pdf_path=str(paper.local_pdf_path) if paper.local_pdf_path else None,
            is_downloaded=paper.is_downloaded,
            raw_metadata_json=json.dumps(paper.raw_metadata),
            topics_json=json.dumps(paper.topics),
            pinned=paper.pinned,
            status=paper.status.value,
            score_relevance=paper.scores.relevance,
            score_novelty=paper.scores.novelty,
            score_velocity=paper.scores.velocity,
            velocity_12w_json=json.dumps(paper.velocity_12w),
            cache_flags=paper.cache_flags,
        )


class CitationORM(Base):
    """Directed citation edge table.

    ``cited_paper_id`` intentionally has no foreign-key constraint: we
    frequently store references to papers that haven't been ingested yet.
    ``citing_paper_id`` references a paper we do own.
    """

    __tablename__ = "citations"

    citing_paper_id: Mapped[str] = mapped_column(String(200), primary_key=True, index=True)
    cited_paper_id: Mapped[str] = mapped_column(String(200), primary_key=True, index=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_influential: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class PaperEdgeORM(Base):
    """LLM-typed or author-derived relationship edges between papers.

    edge_type: extends | supersedes | challenges | applies | uses | surveys | baseline | concurrent
    source: llm | author | venue
    """

    __tablename__ = "paper_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    to_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    edge_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="llm")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
