"""Search endpoints: omnibar + paper lexical search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ...models.paper import PaperORM
from ...models.taxonomy import TOPICS
from ..deps import get_db
from ..schemas import PaperCard

router = APIRouter(tags=["search"])


@router.get("/papers/search", response_model=list[PaperCard])
def search_papers(
    q: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[PaperCard]:
    """Lexical search over title + abstract. Hybrid vector search added in Phase 4."""
    if not q:
        return []

    # Handle prefix operators: title:, author:, topic:, id:
    if q.startswith("id:"):
        term = q[3:].strip()
        orm = db.get(PaperORM, term)
        return [PaperCard.from_paper(orm.to_pydantic())] if orm else []

    if q.startswith("topic:"):
        term = q[6:].strip()
        stmt = (
            select(PaperORM)
            .where(PaperORM.topics_json.contains(term))
            .order_by(PaperORM.published_date.desc())
            .offset(offset)
            .limit(limit)
        )
        return [PaperCard.from_paper(r.to_pydantic()) for r in db.execute(stmt).scalars()]

    if q.startswith("author:"):
        term = q[7:].strip()
        stmt = (
            select(PaperORM)
            .where(PaperORM.authors_json.ilike(f"%{term}%"))
            .order_by(PaperORM.published_date.desc())
            .offset(offset)
            .limit(limit)
        )
        return [PaperCard.from_paper(r.to_pydantic()) for r in db.execute(stmt).scalars()]

    pattern = f"%{q}%"
    stmt = (
        select(PaperORM)
        .where(
            or_(
                PaperORM.title.ilike(pattern),
                PaperORM.abstract.ilike(pattern),
                PaperORM.authors_json.ilike(pattern),
            )
        )
        .order_by(PaperORM.published_date.desc())
        .offset(offset)
        .limit(limit)
    )
    return [PaperCard.from_paper(r.to_pydantic()) for r in db.execute(stmt).scalars()]


@router.get("/search/omni")
def omni_search(
    q: str = Query(default=""),
    limit: int = Query(default=12, ge=1, le=50),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Mixed-type result list for the ⌘K omnibar."""
    if not q:
        return []

    results: list[dict] = []

    # Papers
    pattern = f"%{q}%"
    paper_stmt = (
        select(PaperORM)
        .where(or_(PaperORM.title.ilike(pattern), PaperORM.abstract.ilike(pattern)))
        .order_by(PaperORM.score_relevance.desc())
        .limit(limit)
    )
    for orm in db.execute(paper_stmt).scalars():
        p = orm.to_pydantic()
        results.append(
            {
                "kind": "paper",
                "id": p.id,
                "title": p.title,
                "venue": p.venue,
                "authors": [a.name for a in p.authors][:3],
                "score": p.scores.relevance,
            }
        )

    # Topics
    q_lower = q.lower()
    for topic in TOPICS:
        if q_lower in topic.lower():
            results.append(
                {
                    "kind": "topic",
                    "slug": topic.lower().replace(" ", "_"),
                    "name": topic,
                    "paper_count": 0,
                }
            )

    return results[:limit]
