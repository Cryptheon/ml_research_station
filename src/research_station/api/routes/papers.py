"""Paper endpoints: queue, detail, trace, velocity, citations, pin."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ...database.repository import CitationRepository, SummaryRepository
from ...models.paper import PaperORM
from ..deps import get_db
from ..schemas import (
    CitationEdge,
    CitationGraph,
    PaperCard,
    PaperDetail,
    VelocityOut,
)

router = APIRouter(prefix="/papers", tags=["papers"])


# ── Queue ─────────────────────────────────────────────────────────────────────


@router.get("/queue", response_model=list[PaperCard])
def list_papers(
    q: str = Query(default=""),
    sources: list[str] = Query(default=[]),
    topics: list[str] = Query(default=[]),
    venues: list[str] = Query(default=[]),
    status: list[str] = Query(default=[]),
    pinned: bool | None = Query(default=None),
    since_days: int | None = Query(default=None),
    sort: str = Query(default="date"),
    limit: int = Query(default=50, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[PaperCard]:
    stmt = select(PaperORM)

    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(PaperORM.title.ilike(pattern), PaperORM.abstract.ilike(pattern)))
    if sources:
        stmt = stmt.where(PaperORM.source.in_(sources))
    if venues:
        stmt = stmt.where(PaperORM.venue.in_(venues))
    if status:
        stmt = stmt.where(PaperORM.status.in_(status))
    if pinned is not None:
        stmt = stmt.where(PaperORM.pinned == pinned)
    if since_days is not None:
        from datetime import datetime, timedelta

        cutoff = datetime.utcnow() - timedelta(days=since_days)
        stmt = stmt.where(PaperORM.published_date >= cutoff)
    if topics:
        # JSON text containment check — each topic must appear in topics_json
        for topic in topics:
            stmt = stmt.where(PaperORM.topics_json.contains(topic))

    if sort == "relevance":
        stmt = stmt.order_by(PaperORM.score_relevance.desc())
    elif sort == "novelty":
        stmt = stmt.order_by(PaperORM.score_novelty.desc())
    elif sort == "velocity":
        stmt = stmt.order_by(PaperORM.score_velocity.desc())
    elif sort == "citations":
        stmt = stmt.order_by(PaperORM.citation_count.desc())
    else:
        stmt = stmt.order_by(PaperORM.published_date.desc())

    stmt = stmt.offset(offset).limit(limit)
    rows = db.execute(stmt).scalars().all()
    return [PaperCard.from_paper(row.to_pydantic()) for row in rows]


# ── Sub-resources (must come BEFORE the /{paper_id:path} catch-all) ──────────
# With Starlette's :path converter, /{paper_id:path} greedily matches everything
# after the prefix, including paths like "arxiv:2504.11823/trace". Registering
# more specific routes first ensures they are tried first.


def _get_orm(paper_id: str, db: Session):
    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    return orm


@router.get("/{paper_id:path}/trace")
def get_trace(paper_id: str, db: Session = Depends(get_db)) -> list[dict]:
    _get_orm(paper_id, db)  # raises 404 if paper not found
    summary = SummaryRepository(db).get_latest(paper_id)
    if summary is None or not summary.thinking_trace:
        return []
    return [
        {
            "t": "00:00.00",
            "kind": "reason",
            "label": "LLM thinking",
            "detail": summary.thinking_trace,
        }
    ]


@router.get("/{paper_id:path}/velocity", response_model=VelocityOut)
def get_velocity(paper_id: str, db: Session = Depends(get_db)) -> VelocityOut:
    orm = _get_orm(paper_id, db)
    paper = orm.to_pydantic()
    cites_delta = sum(paper.velocity_12w[-4:]) if len(paper.velocity_12w) >= 4 else 0
    return VelocityOut(
        paper_id=paper_id,
        cited_by=paper.citation_count or 0,
        velocity_12w=paper.velocity_12w,
        cites_delta=cites_delta,
    )


@router.get("/{paper_id:path}/citations", response_model=CitationGraph)
def get_citations(paper_id: str, db: Session = Depends(get_db)) -> CitationGraph:
    orm = _get_orm(paper_id, db)
    citation_repo = CitationRepository(db)
    references = citation_repo.get_references(paper_id)
    citing = citation_repo.get_citing_papers(paper_id)
    edges = [
        CitationEdge(
            from_id=c.citing_paper_id, to_id=c.cited_paper_id, is_influential=c.is_influential
        )
        for c in references + citing
    ]
    neighbour_ids = {c.cited_paper_id for c in references} | {c.citing_paper_id for c in citing}
    neighbour_ids.discard(paper_id)
    nodes: list[PaperCard] = [PaperCard.from_paper(orm.to_pydantic())]
    for nid in neighbour_ids:
        n_orm = db.get(PaperORM, nid)
        if n_orm:
            nodes.append(PaperCard.from_paper(n_orm.to_pydantic()))
    return CitationGraph(nodes=nodes, edges=edges)


@router.get("/{paper_id:path}/similar", response_model=list[PaperCard])
def similar_papers(
    paper_id: str,
    k: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> list[PaperCard]:
    orm = _get_orm(paper_id, db)
    paper = orm.to_pydantic()
    if not paper.topics:
        return []
    stmt = (
        select(PaperORM)
        .where(PaperORM.id != paper_id)
        .order_by(PaperORM.score_relevance.desc())
        .limit(k)
    )
    rows = db.execute(stmt).scalars().all()
    result = [
        PaperCard.from_paper(r.to_pydantic())
        for r in rows
        if any(t in r.topics_json for t in paper.topics)
    ]
    return result[:k]


@router.post("/{paper_id:path}/pin")
def toggle_pin(paper_id: str, db: Session = Depends(get_db)) -> dict:
    orm = _get_orm(paper_id, db)
    orm.pinned = not orm.pinned
    db.commit()
    return {"paper_id": paper_id, "pinned": orm.pinned}


# ── PDF redirect (must be before the catch-all) ──────────────────────────────


@router.get("/{paper_id:path}/pdf.pdf")
def get_pdf(paper_id: str, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse

    orm = _get_orm(paper_id, db)
    # Serve local file if already downloaded
    if orm.local_pdf_path:
        local = Path(orm.local_pdf_path)
        if local.exists():
            return FileResponse(local, media_type="application/pdf", filename=local.name)
    # Fall back to redirect
    if orm.pdf_url:
        return RedirectResponse(url=orm.pdf_url, status_code=302)
    paper = orm.to_pydantic()
    if paper.arxiv_id:
        return RedirectResponse(url=f"https://arxiv.org/pdf/{paper.arxiv_id}", status_code=302)
    raise HTTPException(404, "No PDF available for this paper")


# ── Detail (catch-all — must be last) ────────────────────────────────────────


@router.get("/{paper_id:path}", response_model=PaperDetail)
def get_paper(paper_id: str, db: Session = Depends(get_db)) -> PaperDetail:
    orm = _get_orm(paper_id, db)
    return PaperDetail.from_paper(orm.to_pydantic())
