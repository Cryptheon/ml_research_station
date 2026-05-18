"""Paper reader, fulltext, entities, cache, and PDF proxy endpoints."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config.settings import get_settings
from ...database.repository import SummaryRepository
from ...models.paper import CACHE_FULLTEXT, PaperORM
from ..background import (
    _bg_download_pdf,
    _bg_extract_entities,
    _bg_ocr,
    _bg_summarise,
    _ocr_progress,
    _summarise_progress,
)
from ..deps import get_db
from ..schemas import PaperDetail

logger = logging.getLogger(__name__)

router = APIRouter(tags=["reader"])


@router.get("/papers/{paper_id:path}/reader")
def get_reader(paper_id: str, db: Session = Depends(get_db)) -> dict:
    """Return sections built from the stored LLM summary, or abstract fallback."""
    from ...processing.pdf_ocr import PDFOCRPipeline

    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404, f"Paper '{paper_id}' not found")
    paper = orm.to_pydantic()
    summary = SummaryRepository(db).get_latest(paper_id)

    ocr_available = False
    ocr_page_count = 0
    ocr_meta: dict | None = None
    _settings = get_settings()
    _pdf_path_for_lookup = Path(orm.local_pdf_path) if orm.local_pdf_path else Path(".")
    txt = PDFOCRPipeline.load_text(_pdf_path_for_lookup, paper_id, ocr_dir=_settings.ocr_dir)
    if txt:
        ocr_available = True
        ocr_page_count = txt.count("--- Page ")
        _meta_path = _settings.ocr_dir / f"{PDFOCRPipeline.safe_name(paper_id)}_meta.json"
        if _meta_path.exists():
            try:
                ocr_meta = json.loads(_meta_path.read_text())
            except Exception:
                pass

    sections = []
    if summary:
        sections.append(
            {
                "num": "01",
                "title": "Model summary",
                "hint": "~200 words",
                "defaultOpen": True,
                "blocks": [{"kind": "p", "html": summary.methodology or paper.abstract or ""}],
            }
        )
        if summary.contributions:
            sections.append(
                {
                    "num": "02",
                    "title": "Contributions",
                    "hint": f"{len(summary.contributions)} items",
                    "defaultOpen": True,
                    "blocks": [{"kind": "ul", "items": summary.contributions}],
                }
            )
        if summary.key_results:
            sections.append(
                {
                    "num": "03",
                    "title": "Key claims",
                    "hint": f"{len(summary.key_results)} items",
                    "defaultOpen": True,
                    "blocks": [{"kind": "ul", "items": summary.key_results}],
                }
            )
        if summary.limitations:
            sections.append(
                {
                    "num": "04",
                    "title": "What to question",
                    "hint": None,
                    "defaultOpen": False,
                    "blocks": [{"kind": "ul", "items": summary.limitations}],
                }
            )
        if summary.related_work_context:
            sections.append(
                {
                    "num": "05",
                    "title": "Related lineage",
                    "hint": None,
                    "defaultOpen": False,
                    "blocks": [{"kind": "p", "html": summary.related_work_context}],
                }
            )
        if summary.suggested_follow_up:
            sections.append(
                {
                    "num": "06",
                    "title": "Follow-up",
                    "hint": f"{len(summary.suggested_follow_up)} suggestions",
                    "defaultOpen": False,
                    "blocks": [{"kind": "ul", "items": summary.suggested_follow_up}],
                }
            )
        reader_meta = {
            "model": summary.model_used,
            "provider": summary.provider,
            "generated_at": summary.generated_at.isoformat() if summary.generated_at else None,
            "token_count": (summary.prompt_tokens or 0) + (summary.completion_tokens or 0),
        }
    else:
        sections.append(
            {
                "num": "01",
                "title": "Abstract",
                "hint": None,
                "defaultOpen": True,
                "blocks": [{"kind": "p", "html": paper.abstract or "No abstract available."}],
            }
        )
        if paper.tldr:
            sections.append(
                {
                    "num": "02",
                    "title": "TLDR",
                    "hint": None,
                    "defaultOpen": True,
                    "blocks": [{"kind": "p", "html": paper.tldr}],
                }
            )
        reader_meta = {"model": None, "provider": None, "generated_at": None, "token_count": 0}

    return {
        **PaperDetail.from_paper(paper).model_dump(),
        "sections": sections,
        "figures": [],
        "claims": [],
        "reader_meta": reader_meta,
        "ocr_available": ocr_available,
        "ocr_page_count": ocr_page_count,
        "ocr_meta": ocr_meta,
        "externalUrl": (
            f"https://arxiv.org/abs/{paper.arxiv_id}"
            if paper.arxiv_id
            else paper.raw_metadata.get("page_url")
            if paper.source.value == "wikipedia"
            else None
        ),
        "abstractHtml": paper.abstract or "",
    }


@router.post("/papers/{paper_id:path}/reader/regenerate", status_code=202)
async def regenerate_reader(
    paper_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404, f"Paper '{paper_id}' not found")
    if not (orm.cache_flags or 0) & CACHE_FULLTEXT:
        raise HTTPException(
            422,
            "No extracted text found for this paper. Run OCR or PDF extract first, then summarise.",
        )
    settings = get_settings()
    background_tasks.add_task(_bg_summarise, paper_id, settings)
    return {"job_id": f"regen-{paper_id}", "status": "running"}


@router.get("/papers/{paper_id:path}/summarise/progress")
def get_summarise_progress(paper_id: str) -> dict:
    """Return current map-reduce chunk progress for an in-flight summarisation."""
    prog = _summarise_progress.get(paper_id)
    if prog is None:
        return {"active": False}
    return {"active": True, **prog}


@router.get("/papers/{paper_id:path}/fulltext")
def get_fulltext(paper_id: str, db: Session = Depends(get_db)) -> dict:
    """Return cached full text (OCR, extracted PDF, or Wikipedia article)."""
    from ...processing.pdf_ocr import PDFOCRPipeline

    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404, f"Paper '{paper_id}' not found")

    settings = get_settings()
    pdf_path_for_lookup = Path(orm.local_pdf_path) if orm.local_pdf_path else Path(".")
    text = PDFOCRPipeline.load_text(pdf_path_for_lookup, paper_id, ocr_dir=settings.ocr_dir)
    if not text:
        raise HTTPException(404, "No fulltext cached for this paper")

    if paper_id.startswith("wikipedia:"):
        source = "wikipedia"
    elif paper_id.startswith("web:"):
        source = "web"
    else:
        source = "ocr"

    page_url = None
    if source in ("wikipedia", "web"):
        try:
            raw_meta = json.loads(orm.raw_metadata_json or "{}")
            page_url = raw_meta.get("page_url") or raw_meta.get("url")
        except Exception:
            pass

    return {"text": text, "char_count": len(text), "source": source, "page_url": page_url}


@router.get("/papers/{paper_id:path}/entities")
def get_entities(paper_id: str, db: Session = Depends(get_db)) -> dict:
    """Return LLM-extracted entities and typed relationships for a paper."""
    from ...models.entity import EntityRelationshipORM as _RelORM
    from ...models.entity import PaperEntityORM as _EntityORM

    orm_ents = db.execute(select(_EntityORM).where(_EntityORM.paper_id == paper_id)).scalars().all()

    if not orm_ents:
        return {"paper_id": paper_id, "entities": [], "relationships": []}

    id_to_name = {e.id: e.name for e in orm_ents}
    entities = [
        {
            "id": e.id,
            "name": e.name,
            "type": e.entity_type,
            "attributes": json.loads(e.attributes_json),
        }
        for e in orm_ents
    ]

    orm_rels = (
        db.execute(select(_RelORM).where(_RelORM.source_paper_id == paper_id)).scalars().all()
    )
    relationships = [
        {
            "from": id_to_name.get(r.from_entity_id, str(r.from_entity_id)),
            "to": id_to_name.get(r.to_entity_id, str(r.to_entity_id)),
            "type": r.relationship_type,
            "description": r.description,
            "confidence": r.confidence,
        }
        for r in orm_rels
    ]

    return {"paper_id": paper_id, "entities": entities, "relationships": relationships}


@router.post("/papers/{paper_id:path}/entities/extract")
async def trigger_entity_extraction(
    paper_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Trigger entity extraction for a paper (runs in background)."""
    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404, f"Paper '{paper_id}' not found")
    settings = get_settings()
    background_tasks.add_task(_bg_extract_entities, paper_id, settings)
    return {"status": "queued", "paper_id": paper_id}


@router.get("/papers/{paper_id:path}/cache")
def get_cache(paper_id: str, db: Session = Depends(get_db)) -> dict:
    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404, f"Paper '{paper_id}' not found")
    f = orm.cache_flags or 0
    summary = SummaryRepository(db).has_summary(paper_id)
    return {
        "pdf": bool(f & 1) or bool(orm.is_downloaded),
        "embeddings": bool(f & 2),
        "summary": summary or bool(f & 4),
        "figures": False,
        "references": orm.reference_count is not None,
        "fulltext": bool(f & CACHE_FULLTEXT),
    }


@router.delete("/papers/{paper_id:path}/cache", status_code=204)
def evict_cache(paper_id: str, db: Session = Depends(get_db)) -> None:
    orm = db.get(PaperORM, paper_id)
    if orm:
        orm.cache_flags = 0
        orm.is_downloaded = False
        orm.local_pdf_path = None
        db.commit()


@router.post("/papers/{paper_id:path}/ingest", status_code=202)
async def reingest_paper(
    paper_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404, f"Paper '{paper_id}' not found")
    paper = orm.to_pydantic()
    if paper.source.value == "wikipedia" or (not paper.pdf_url and not paper.arxiv_id):
        raise HTTPException(
            422, "This item has no PDF. Wikipedia articles provide full text directly."
        )
    settings = get_settings()
    background_tasks.add_task(_bg_download_pdf, paper_id, settings)
    return {"job_id": f"ingest-{paper_id}", "status": "running"}


@router.post("/papers/{paper_id:path}/ocr", status_code=202)
async def ocr_paper(
    paper_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Trigger vision-LLM OCR on the paper's local PDF.

    Requires: PDF already downloaded (use /ingest first).
    Requires: OCR__PROVIDER=ollama or vllm in .env.
    """
    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404, f"Paper '{paper_id}' not found")
    if not orm.local_pdf_path:
        raise HTTPException(422, "PDF not downloaded — run /ingest first")
    settings = get_settings()
    ocr_provider = (settings.ocr.provider or settings.llm.provider).lower()
    if ocr_provider not in ("ollama", "vllm"):
        raise HTTPException(
            422,
            f"OCR requires provider=ollama or vllm "
            f"(current: {ocr_provider}). "
            "Set OCR__PROVIDER=ollama or OCR__PROVIDER=vllm in .env.",
        )
    background_tasks.add_task(_bg_ocr, paper_id, settings, False)
    return {"job_id": f"ocr-{paper_id}", "status": "running"}


@router.get("/papers/{paper_id:path}/ocr/progress")
def ocr_progress(paper_id: str) -> dict:
    """Return live OCR progress for the given paper."""
    prog = _ocr_progress.get(paper_id, {})
    return {
        "running": prog.get("running", False),
        "pages_done": prog.get("pages_done", 0),
        "pages_total": prog.get("pages_total", 0),
    }


@router.get("/papers/{paper_id:path}/pdf.pdf")
def get_pdf(paper_id: str, db: Session = Depends(get_db)):
    orm = db.get(PaperORM, paper_id)
    if orm is None:
        raise HTTPException(404)
    if orm.local_pdf_path:
        local = Path(orm.local_pdf_path)
        if local.exists():
            return FileResponse(
                path=str(local),
                media_type="application/pdf",
                headers={"Content-Disposition": f'inline; filename="{local.name}"'},
            )
    if orm.pdf_url:
        return RedirectResponse(url=orm.pdf_url, status_code=302)
    paper = orm.to_pydantic()
    if paper.arxiv_id:
        return RedirectResponse(url=f"https://arxiv.org/pdf/{paper.arxiv_id}", status_code=302)
    raise HTTPException(404, "No PDF available")
