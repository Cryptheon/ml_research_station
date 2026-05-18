"""Ingest endpoints.

POST /ingest/run       → 202 + {job_id}
POST /ingest/paper     → fetch single paper by arXiv ID/URL
GET  /ingest/summary   → last run stats
GET  /ingest/active    → running jobs
POST /ingest/plan      → cost/step estimate
WS   /ws/ingest/{id}   → real-time frame stream
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time

logger = logging.getLogger(__name__)
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import arxiv as arxiv_lib
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from ...config.settings import get_settings
from ...database.engine import build_engine, build_session_factory, get_session
from ...database.repository import CitationRepository, PaperRepository
from ...ingestion.arxiv_fetcher import ArxivFetcher
from ...ingestion.openalex_enricher import OpenAlexClient
from ...ingestion.pipeline import IngestionPipeline
from ...ingestion.semantic_scholar import SemanticScholarClient
from ...models.paper import Paper, PaperORM
from ...models.taxonomy import classify as classify_topics
from ...models.user import IngestHistoryORM, ManuallyAddedPaperORM
from ..deps import get_db
from ..jobs import IngestJob, create_job, get_job
from ..schemas import PaperCard

router = APIRouter(tags=["ingest"])

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ingest")

_SOURCE_MAP = {
    "arXiv": "arxiv",
    "bioRxiv": "biorxiv",
    "OpenReview": "openreview",
    "NeurIPS": "openreview",
    "ICML": "openreview",
    "ICLR": "openreview",
    "COLM": "openreview",
    "PubMed": "pubmed",
    "Wikipedia": "wikipedia",
    "Nature": None,  # paywalled — skip
}


# ── Request / response schemas ────────────────────────────────────────────────


class IngestRunRequest(BaseModel):
    interests: list[str] = []
    sources: list[str] = []
    window_days: int = 14
    date_from: str | None = None  # ISO-8601 date e.g. "2024-01-01"
    date_to: str | None = None  # ISO-8601 date e.g. "2024-06-30"
    save_as_watch: bool = False
    arxiv_categories: list[str] | None = None
    biorxiv_categories: list[str] | None = None
    wikipedia_languages: list[str] | None = None


class IngestPlanRequest(BaseModel):
    interests: list[str] = []
    sources: list[str] = []
    window_days: int = 14
    date_from: str | None = None
    date_to: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _resolve_sources(names: list[str]) -> list[str]:
    """Map frontend source display names to pipeline source keys."""
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        key = _SOURCE_MAP.get(n)
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out or ["arxiv"]


def _run_pipeline_sync(job: IngestJob, req: IngestRunRequest) -> None:
    """Runs in a thread — pushes IngestFrames to the job queue."""
    t0 = time.time()
    job.status = "running"
    settings = get_settings()
    sources = _resolve_sources(req.sources)

    def emit(frame: dict) -> None:
        job.push(frame)

    try:
        # Resolve date range — explicit dates take priority over window_days
        logger.info(
            "Ingest request: interests=%s window_days=%d date_from=%r date_to=%r",
            req.interests,
            req.window_days,
            req.date_from,
            req.date_to,
        )
        date_from: datetime | None = None
        date_to: datetime | None = None
        if req.date_from:
            date_from = datetime.fromisoformat(req.date_from).replace(tzinfo=timezone.utc)
        if req.date_to:
            # End of day so papers submitted any time on the end date are included
            date_to = datetime.fromisoformat(req.date_to).replace(
                tzinfo=timezone.utc, hour=23, minute=59, second=59
            )

        if date_from and date_to:
            range_note = f"{req.date_from} → {req.date_to}"
        else:
            range_note = f"last {req.window_days}d"

        emit({"type": "phase", "name": "query", "note": f"{len(sources)} source(s) · {range_note}"})

        pipeline = IngestionPipeline(settings)
        result = pipeline.run(
            days_lookback=req.window_days,
            date_from=date_from,
            date_to=date_to,
            sources=sources,
            interests=req.interests or None,
            arxiv_categories=req.arxiv_categories or None,
            biorxiv_categories=req.biorxiv_categories or None,
            wikipedia_languages=req.wikipedia_languages or None,
            enrich=True,
            dry_run=False,
            download_pdfs=True,
            progress_callback=emit,
        )

        emit({"type": "phase", "name": "rank", "note": "cosine + recency + venue weight"})

        # Stream new papers so left rail can prepend them live.
        # Use created_at (ingestion time) not published_date, so historical
        # date-range pulls still stream their newly-saved papers.
        run_start_utc = datetime.fromtimestamp(t0, tz=timezone.utc)
        engine = build_engine(settings.database.sqlite_path)
        factory = build_session_factory(engine)
        new_paper_ids: list[str] = []
        with get_session(factory) as db:
            stmt = (
                sa_select(PaperORM)
                .where(PaperORM.created_at >= run_start_utc)
                .order_by(PaperORM.created_at.desc())
                .limit(50)
            )
            just_added = db.execute(stmt).scalars().all()
            to_emit = just_added[: result.total_new]
            # Collect IDs inside the session — accessing ORM attributes after
            # session.close() raises DetachedInstanceError (expire_on_commit=True).
            new_paper_ids = [orm.id for orm in to_emit]
            for orm in to_emit:
                emit(
                    {
                        "type": "paper",
                        "partial": PaperCard.from_paper(orm.to_pydantic()).model_dump(),
                    }
                )

        job.found = result.total_new
        job.scanned = result.total_fetched
        job.duration_ms = (time.time() - t0) * 1000
        job.status = "done"

        # Persist ingest history with exact paper IDs so the frontend can filter
        # by run without relying on an imprecise time-window heuristic.
        try:
            hist_engine = build_engine(settings.database.sqlite_path)
            hist_factory = build_session_factory(hist_engine)
            with get_session(hist_factory) as hist_db:
                hist_db.add(
                    IngestHistoryORM(
                        user_id="default",
                        interests_json=json.dumps(req.interests),
                        sources_json=json.dumps(sources),
                        found=result.total_new,
                        scanned=result.total_fetched,
                        duration_seconds=(time.time() - t0),
                        ran_at=datetime.fromtimestamp(t0, tz=timezone.utc),
                        paper_ids_json=json.dumps(new_paper_ids),
                    )
                )
        except Exception:
            pass

        emit(
            {
                "type": "done",
                "found": job.found,
                "scanned": job.scanned,
                "duration_ms": job.duration_ms,
            }
        )

    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
        emit({"type": "error", "message": str(exc)})


# ── Single-paper ingest ───────────────────────────────────────────────────────


class IngestPaperRequest(BaseModel):
    arxiv_id: str  # accepts "2304.00001", "arxiv:2304.00001", or full abs URL


def _parse_arxiv_id(raw: str) -> str:
    """Extract a bare arXiv ID from any common input format."""
    raw = raw.strip()
    # Full URL: https://arxiv.org/abs/2304.00001 or /pdf/2304.00001
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", raw, re.I)
    if m:
        return m.group(1).split("v")[0]
    # Prefixed: arxiv:2304.00001 or arXiv:2304.00001v2
    m = re.match(r"arxiv:\s*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", raw, re.I)
    if m:
        return m.group(1).split("v")[0]
    # Bare ID (old or new format): 2304.00001 or 2304.00001v2 or hep-th/9901001
    m = re.match(r"^([0-9]{4}\.[0-9]{4,5})(?:v\d+)?$", raw)
    if m:
        return m.group(1)
    m = re.match(r"^([a-z\-]+/[0-9]{7})(?:v\d+)?$", raw, re.I)
    if m:
        return m.group(1)
    raise ValueError(f"Cannot parse arXiv ID from: {raw!r}")


@router.post("/ingest/paper")
def ingest_single_paper(req: IngestPaperRequest, db: Session = Depends(get_db)) -> dict:
    """Fetch and store a single arXiv paper by ID or URL."""
    try:
        arxiv_id = _parse_arxiv_id(req.arxiv_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    settings = get_settings()
    client = arxiv_lib.Client(page_size=1, delay_seconds=1, num_retries=3)
    search = arxiv_lib.Search(id_list=[arxiv_id])
    results = list(client.results(search))
    if not results:
        raise HTTPException(status_code=404, detail=f"arXiv paper not found: {arxiv_id}")

    entry = results[0]
    fetcher = ArxivFetcher(settings.rate_limits)
    paper: Paper = fetcher._entry_to_paper(entry)

    if not paper.topics:
        paper.topics = classify_topics(paper.title, paper.abstract)

    # Enrich with OpenAlex / S2 (best-effort — don't fail the whole request)
    openalex = OpenAlexClient(delay_seconds=0.12)
    try:
        if settings.semantic_scholar_api_key:
            s2 = SemanticScholarClient(settings.semantic_scholar_api_key, settings.rate_limits)
            paper = s2.enrich_paper(paper)
        paper = openalex.enrich_paper(paper)
    except Exception as exc:
        logger.warning("Enrichment failed for %s (non-fatal): %s", arxiv_id, exc)

    paper_repo = PaperRepository(db)
    citation_repo = CitationRepository(db)
    paper_repo.upsert_many([paper])

    # Fetch citation edges (best-effort)
    try:
        if paper.semantic_scholar_id and settings.semantic_scholar_api_key:
            s2 = SemanticScholarClient(settings.semantic_scholar_api_key, settings.rate_limits)
            refs = s2.get_references(paper)
        else:
            refs = openalex.get_references(paper)
        citation_repo.upsert_many(refs)
    except Exception as exc:
        logger.warning("Citation fetch failed for %s (non-fatal): %s", arxiv_id, exc)

    # Record as manually added (upsert — idempotent for re-adds)
    if db.get(ManuallyAddedPaperORM, paper.id) is None:
        db.add(ManuallyAddedPaperORM(paper_id=paper.id))

    # Flush so pending objects become persistent and visible to session.get()
    db.flush()
    orm = db.get(PaperORM, paper.id)
    if not orm:
        raise HTTPException(status_code=500, detail="Paper saved but could not retrieve from DB")

    return {"paper": PaperCard.from_paper(orm.to_pydantic()).model_dump()}


# ── REST endpoints ────────────────────────────────────────────────────────────


@router.post("/ingest/run", status_code=202)
async def run_ingest(req: IngestRunRequest) -> dict:
    job = create_job()
    loop = asyncio.get_event_loop()
    job.attach_loop(loop)

    # Save interests to localStorage equivalent (update ingest stats later)
    loop.run_in_executor(_executor, _run_pipeline_sync, job, req)
    return {"job_id": job.id}


@router.get("/ingest/summary")
def ingest_summary() -> dict:
    settings = get_settings()
    engine = build_engine(settings.database.sqlite_path)
    factory = build_session_factory(engine)
    with get_session(factory) as db:
        count = PaperRepository(db).count()
    return {
        "last_run_at": None,
        "interests_last_used": [],
        "found": 0,
        "scanned": 0,
        "total_papers": count,
    }


@router.get("/ingest/active")
def ingest_active() -> dict:
    from ..jobs import _store

    running = [
        {"id": j.id, "started_at": None, "progress": j.status}
        for j in _store.values()
        if j.status in ("pending", "running")
    ]
    return {"jobs": running}


@router.post("/ingest/plan")
def ingest_plan(req: IngestPlanRequest) -> dict:
    sources = _resolve_sources(req.sources)
    n_interests = max(len(req.interests), 1)
    n_sources = max(len(sources), 1)
    candidates = n_interests * 120 * n_sources
    summarise = min(candidates, 40)
    settings = get_settings()
    return {
        "estimate_candidates": candidates,
        "estimate_summarise": summarise,
        "steps": [
            {
                "step": "embed",
                "note": f"{n_interests} interest vector{'s' if n_interests > 1 else ''}",
            },
            {
                "step": "query",
                "note": f"{n_sources} source{'s' if n_sources > 1 else ''} · last {req.window_days}d",
            },
            {"step": "rank", "note": "cosine + recency + venue weight"},
            {"step": "dedup", "note": "title + DOI hash"},
            {"step": "summarise", "note": f"top ~{summarise} via {settings.llm.provider}"},
        ],
        "model_for_summary": settings.llm.model_name,
    }


# ── WebSocket stream ──────────────────────────────────────────────────────────


@router.websocket("/ws/ingest/{job_id}")
async def ws_ingest(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()
    job = get_job(job_id)
    if job is None:
        await websocket.send_text(json.dumps({"type": "error", "message": "Job not found"}))
        await websocket.close()
        return

    try:
        while True:
            frame = await asyncio.wait_for(job.next_frame(), timeout=600.0)
            await websocket.send_text(json.dumps(frame))
            if frame.get("type") in ("done", "error"):
                break
    except asyncio.TimeoutError:
        await websocket.send_text(json.dumps({"type": "error", "message": "Job timed out"}))
    except WebSocketDisconnect:
        pass
