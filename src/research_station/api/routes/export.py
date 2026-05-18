"""Paper export endpoints: BibTeX, Markdown, JSON, Obsidian."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from ...database.repository import SummaryRepository
from ...models.paper import PaperORM
from ..deps import get_db
from ..schemas import PaperDetail

logger = logging.getLogger(__name__)

router = APIRouter(tags=["export"])


@router.get("/papers/{paper_id:path}/export.bib")
def export_bib(paper_id: str, db: Session = Depends(get_db)) -> PlainTextResponse:
    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404)
    p = orm.to_pydantic()
    key = (p.arxiv_id or p.id).replace(":", "_").replace("/", "_")
    authors = " and ".join(a.name for a in p.authors)
    bib = (
        f"@article{{{key},\n"
        f"  title   = {{{p.title}}},\n"
        f"  author  = {{{authors}}},\n"
        f"  year    = {{{p.published_date.year}}},\n"
        f"  journal = {{{p.venue or p.source.value}}},\n"
        + (f"  doi     = {{{p.doi}}},\n" if p.doi else "")
        + (f"  url     = {{https://arxiv.org/abs/{p.arxiv_id}}},\n" if p.arxiv_id else "")
        + "}\n"
    )
    return PlainTextResponse(
        bib,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{key}.bib"'},
    )


@router.get("/papers/{paper_id:path}/export.md")
def export_md(paper_id: str, db: Session = Depends(get_db)) -> PlainTextResponse:
    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404)
    p = orm.to_pydantic()
    summary = SummaryRepository(db).get_latest(paper_id)
    lines = [
        f"# {p.title}",
        f"**Authors:** {', '.join(a.name for a in p.authors)}",
        f"**Venue:** {p.venue or '—'}  **Year:** {p.published_date.year}",
        f"**ID:** `{p.id}`",
        "",
        "## Abstract",
        p.abstract or "",
    ]
    if summary:
        lines += ["", "## Summary", summary.tldr or ""]
        if summary.contributions:
            lines += ["", "## Contributions"]
            lines += [f"- {c}" for c in summary.contributions]
        if summary.key_results:
            lines += ["", "## Key Results"]
            lines += [f"- {r}" for r in summary.key_results]
    return PlainTextResponse(
        "\n".join(lines),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{p.id.replace(":", "_")}.md"'},
    )


@router.get("/papers/{paper_id:path}/export.json")
def export_json(paper_id: str, db: Session = Depends(get_db)) -> dict:
    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404)
    detail = PaperDetail.from_paper(orm.to_pydantic())
    summary = SummaryRepository(db).get_latest(paper_id)
    return {
        "paper": detail.model_dump(),
        "summary": summary.model_dump() if summary else None,
    }


@router.get("/papers/{paper_id:path}/export.obsidian")
def export_obsidian(paper_id: str, db: Session = Depends(get_db)) -> PlainTextResponse:
    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404)
    p = orm.to_pydantic()
    summary = SummaryRepository(db).get_latest(paper_id)
    lines = [
        "---",
        f'title: "{p.title}"',
        f"authors: [{', '.join(a.name for a in p.authors)}]",
        f"year: {p.published_date.year}",
        f"venue: {p.venue or ''}",
        f"tags: [{', '.join(p.topics)}]",
        f"arxiv: {p.arxiv_id or ''}",
        "---",
        "",
        f"# {p.title}",
        "",
        p.abstract or "",
    ]
    if summary and summary.tldr:
        lines += ["", "## TLDR", summary.tldr]
    return PlainTextResponse(
        "\n".join(lines),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{p.id.replace(":", "_")}.md"'},
    )
