"""Batch processing, embedding, and system status endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config.settings import get_settings
from ...models.paper import CACHE_EMBEDDINGS, CACHE_PDF, PaperORM
from ..background import (
    _batch_state,
    _bg_batch,
    _bg_embed,
    _bg_embed_batch,
    _ocr_progress,
)
from ..deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["processing"])


@router.post("/papers/{paper_id:path}/embed", status_code=202)
def embed_paper(
    paper_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404)
    settings = get_settings()
    background_tasks.add_task(_bg_embed, paper_id, settings)
    return {"status": "queued", "paper_id": paper_id}


@router.post("/papers/embed/batch", status_code=202)
def embed_batch(background_tasks: BackgroundTasks) -> dict:
    """Embed all papers that don't have embeddings yet."""
    settings = get_settings()
    background_tasks.add_task(_bg_embed_batch, settings)
    return {"status": "queued"}


@router.get("/papers/manually-added")
def list_manually_added(db: Session = Depends(get_db)) -> list[str]:
    """Return paper IDs added manually (via ingest/paper or agent auto-ingest)."""
    from ...models.user import ManuallyAddedPaperORM

    rows = db.execute(select(ManuallyAddedPaperORM)).scalars().all()
    return [r.paper_id for r in rows]


@router.get("/papers/embed/status")
def embed_status(db: Session = Depends(get_db)) -> dict:
    total = db.execute(select(PaperORM)).scalars().all()
    embedded = sum(1 for p in total if (p.cache_flags or 0) & CACHE_EMBEDDINGS)
    return {"embedded": embedded, "total": len(total)}


@router.get("/batch/status")
def batch_status() -> dict:
    return dict(_batch_state)


@router.get("/processing/status")
def processing_status() -> dict:
    """Aggregate view of all in-flight operations — batch + individual OCR."""
    items: list[dict] = []
    for paper_id, prog in _ocr_progress.items():
        if prog.get("running"):
            items.append(
                {
                    "paper_id": paper_id,
                    "action": "ocr",
                    "pages_done": prog.get("pages_done", 0),
                    "pages_total": prog.get("pages_total", 0),
                    "pct": int((prog["pages_done"] / prog["pages_total"]) * 100)
                    if prog.get("pages_total")
                    else 0,
                }
            )
    if _batch_state.get("running") and _batch_state.get("current"):
        total = _batch_state["total"] or 1
        items.append(
            {
                "paper_id": _batch_state["current"],
                "action": _batch_state.get("action", "batch"),
                "done": _batch_state["done"],
                "total": _batch_state["total"],
                "pct": int((_batch_state["done"] / total) * 100),
            }
        )
    return {"items": items}


class BatchRequest(BaseModel):
    action: str  # "ocr" | "summarize" | "ocr_summarize" | "extract" | "embed" | "download_pdf"
    filter: str = "all"  # "all" | "no_ocr" | "no_summary" | "no_embed" | "no_pdf"
    paper_ids: list[str] | None = None


@router.post("/batch/process", status_code=202)
async def batch_process(
    body: BatchRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Queue a batch OCR/summarize run over the paper corpus."""
    if _batch_state["running"]:
        raise HTTPException(409, "A batch job is already running")

    from sqlalchemy import select as sa_select

    from ...models.paper import CACHE_FULLTEXT as _CACHE_FULLTEXT
    from ...models.paper import PaperORM as _PaperORM

    if body.paper_ids:
        ids = body.paper_ids
    else:
        stmt = sa_select(_PaperORM.id)
        if body.filter == "no_ocr":
            stmt = stmt.where((_PaperORM.cache_flags.op("&")(_CACHE_FULLTEXT)) == 0)
        elif body.filter == "no_summary":
            summarised_ids = sa_select(_PaperORM.id).where((_PaperORM.cache_flags.op("&")(4)) != 0)
            stmt = stmt.where(_PaperORM.id.not_in(summarised_ids))
        elif body.filter == "no_embed":
            stmt = stmt.where((_PaperORM.cache_flags.op("&")(CACHE_EMBEDDINGS)) == 0)
        elif body.filter == "no_pdf":
            stmt = stmt.where((_PaperORM.cache_flags.op("&")(CACHE_PDF)) == 0).where(
                _PaperORM.is_downloaded == False  # noqa: E712
            )
        ids = [row[0] for row in db.execute(stmt).fetchall()]

    if not ids:
        return {"queued": 0, "message": "No papers matched the filter"}

    settings = get_settings()
    background_tasks.add_task(_bg_batch, ids, body.action, settings)
    return {"queued": len(ids), "action": body.action, "filter": body.filter}
