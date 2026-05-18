"""Repository classes providing typed CRUD access to Paper and Citation tables.

Repositories encapsulate all SQL logic so callers (pipeline, API routes) never
write raw queries.  Each repository is instantiated with an active Session and
does not manage commit/rollback — that responsibility belongs to the caller via
the ``get_session`` context manager.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models.paper import Citation, CitationORM, Paper, PaperORM
from ..models.summary import PaperSummary, PaperSummaryORM

logger = logging.getLogger(__name__)


class PaperRepository:
    """CRUD operations for the ``papers`` table."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ── Write ─────────────────────────────────────────────────────────────

    def upsert(self, paper: Paper) -> PaperORM:
        """Insert a paper or update enrichment fields if it already exists."""
        existing = self._session.get(PaperORM, paper.id)
        if existing is None:
            orm = PaperORM.from_pydantic(paper)
            self._session.add(orm)
            return orm
        return self._apply_enrichment(existing, paper)

    def upsert_many(self, papers: list[Paper]) -> int:
        """Upsert a batch of papers; returns count of newly inserted rows."""
        new_count = 0
        for paper in papers:
            existing = self._session.get(PaperORM, paper.id)
            if existing is None:
                self._session.add(PaperORM.from_pydantic(paper))
                new_count += 1
            else:
                self._apply_enrichment(existing, paper)
        return new_count

    def mark_downloaded(self, paper_id: str, local_path: str) -> None:
        """Record that a PDF has been saved locally."""
        orm = self._session.get(PaperORM, paper_id)
        if orm is not None:
            orm.is_downloaded = True
            orm.local_pdf_path = local_path

    # ── Read ──────────────────────────────────────────────────────────────

    def get(self, paper_id: str) -> Paper | None:
        orm = self._session.get(PaperORM, paper_id)
        return orm.to_pydantic() if orm is not None else None

    def get_by_arxiv_id(self, arxiv_id: str) -> Paper | None:
        stmt = select(PaperORM).where(PaperORM.arxiv_id == arxiv_id)
        orm = self._session.execute(stmt).scalar_one_or_none()
        return orm.to_pydantic() if orm is not None else None

    def list_recent(self, days: int = 7, limit: int = 200) -> list[Paper]:
        """Return papers published within the last *days* calendar days."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        stmt = (
            select(PaperORM)
            .where(PaperORM.published_date >= cutoff)
            .order_by(PaperORM.published_date.desc())
            .limit(limit)
        )
        return [row.to_pydantic() for row in self._session.execute(stmt).scalars()]

    def search(
        self,
        query: str = "",
        sources: list[str] | None = None,
        venues: list[str] | None = None,
        categories: list[str] | None = None,
        since_days: int | None = None,
        limit: int = 50,
    ) -> list[Paper]:
        """Flexible search over title + abstract with optional filters."""
        stmt = select(PaperORM)

        if query:
            pattern = f"%{query}%"
            stmt = stmt.where(
                or_(
                    PaperORM.title.ilike(pattern),
                    PaperORM.abstract.ilike(pattern),
                )
            )
        if sources:
            stmt = stmt.where(PaperORM.source.in_(sources))
        if venues:
            stmt = stmt.where(PaperORM.venue.in_(venues))
        if since_days is not None:
            cutoff = datetime.utcnow() - timedelta(days=since_days)
            stmt = stmt.where(PaperORM.published_date >= cutoff)

        stmt = stmt.order_by(PaperORM.published_date.desc()).limit(limit)
        return [row.to_pydantic() for row in self._session.execute(stmt).scalars()]

    def count(self) -> int:
        return self._session.execute(select(func.count()).select_from(PaperORM)).scalar_one()

    def count_by_source(self) -> dict[str, int]:
        rows = self._session.execute(
            select(PaperORM.source, func.count().label("n")).group_by(PaperORM.source)
        ).all()
        return {row.source: row.n for row in rows}

    # ── Private ───────────────────────────────────────────────────────────

    @staticmethod
    def _apply_enrichment(existing: PaperORM, paper: Paper) -> PaperORM:
        """Merge richer data from *paper* into an already-stored *existing* row.

        Only overwrites NULL / zero fields so that a second fetch never
        degrades data already enriched by Semantic Scholar.
        """
        if paper.citation_count is not None and (
            existing.citation_count is None or paper.citation_count > existing.citation_count
        ):
            existing.citation_count = paper.citation_count
        if paper.reference_count is not None and (
            existing.reference_count is None or paper.reference_count > existing.reference_count
        ):
            existing.reference_count = paper.reference_count
        if (
            paper.influential_citation_count is not None
            and existing.influential_citation_count is None
        ):
            existing.influential_citation_count = paper.influential_citation_count
        if paper.semantic_scholar_id and not existing.semantic_scholar_id:
            existing.semantic_scholar_id = paper.semantic_scholar_id
        if paper.tldr and not existing.tldr:
            existing.tldr = paper.tldr
        if paper.abstract and not existing.abstract:
            existing.abstract = paper.abstract
        if paper.venue and not existing.venue:
            existing.venue = paper.venue
        if paper.velocity_12w and not json.loads(existing.velocity_12w_json or "[]"):
            existing.velocity_12w_json = json.dumps(paper.velocity_12w)
        if paper.pdf_url and not existing.pdf_url:
            existing.pdf_url = paper.pdf_url
        return existing


class CitationRepository:
    """CRUD operations for the ``citations`` edge table."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_many(self, citations: list[Citation]) -> int:
        """Insert citation edges, skipping duplicates; returns new edge count."""
        new_count = 0
        for citation in citations:
            pk = (citation.citing_paper_id, citation.cited_paper_id)
            if self._session.get(CitationORM, pk) is None:
                self._session.add(
                    CitationORM(
                        citing_paper_id=citation.citing_paper_id,
                        cited_paper_id=citation.cited_paper_id,
                        context=citation.context,
                        is_influential=citation.is_influential,
                    )
                )
                new_count += 1
        return new_count

    def get_references(self, paper_id: str) -> list[Citation]:
        """Return outgoing edges: papers that *paper_id* cites."""
        stmt = select(CitationORM).where(CitationORM.citing_paper_id == paper_id)
        return [
            Citation(
                citing_paper_id=row.citing_paper_id,
                cited_paper_id=row.cited_paper_id,
                context=row.context,
                is_influential=row.is_influential,
            )
            for row in self._session.execute(stmt).scalars()
        ]

    def get_citing_papers(self, paper_id: str) -> list[Citation]:
        """Return incoming edges: papers that cite *paper_id*."""
        stmt = select(CitationORM).where(CitationORM.cited_paper_id == paper_id)
        return [
            Citation(
                citing_paper_id=row.citing_paper_id,
                cited_paper_id=row.cited_paper_id,
                context=row.context,
                is_influential=row.is_influential,
            )
            for row in self._session.execute(stmt).scalars()
        ]

    def count(self) -> int:
        return self._session.execute(select(func.count()).select_from(CitationORM)).scalar_one()


class SummaryRepository:
    """CRUD operations for the ``paper_summaries`` table."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, summary: PaperSummary) -> PaperSummaryORM:
        """Persist a new summary row (always inserts — multiple per paper OK)."""
        orm = PaperSummaryORM.from_pydantic(summary)
        self._session.add(orm)
        return orm

    def get_latest(self, paper_id: str) -> PaperSummary | None:
        """Return the most recently generated summary for *paper_id*."""
        stmt = (
            select(PaperSummaryORM)
            .where(PaperSummaryORM.paper_id == paper_id)
            .order_by(PaperSummaryORM.generated_at.desc())
            .limit(1)
        )
        orm = self._session.execute(stmt).scalar_one_or_none()
        return orm.to_pydantic() if orm is not None else None

    def list_for_paper(self, paper_id: str) -> list[PaperSummary]:
        """Return all summaries for *paper_id*, newest first."""
        stmt = (
            select(PaperSummaryORM)
            .where(PaperSummaryORM.paper_id == paper_id)
            .order_by(PaperSummaryORM.generated_at.desc())
        )
        return [row.to_pydantic() for row in self._session.execute(stmt).scalars()]

    def has_summary(self, paper_id: str, model: str | None = None) -> bool:
        """Return True if *paper_id* already has at least one summary."""
        stmt = (
            select(func.count())
            .select_from(PaperSummaryORM)
            .where(PaperSummaryORM.paper_id == paper_id)
        )
        if model:
            stmt = stmt.where(PaperSummaryORM.model_used == model)
        return self._session.execute(stmt).scalar_one() > 0

    def count(self) -> int:
        return self._session.execute(select(func.count()).select_from(PaperSummaryORM)).scalar_one()

    def list_unsummarised_paper_ids(self, limit: int = 50) -> list[str]:
        """Return IDs of papers that have no summary yet."""
        summarised = select(PaperSummaryORM.paper_id).distinct()
        stmt = (
            select(PaperORM.id)
            .where(PaperORM.id.not_in(summarised))
            .order_by(PaperORM.published_date.desc())
            .limit(limit)
        )
        return list(self._session.execute(stmt).scalars())
