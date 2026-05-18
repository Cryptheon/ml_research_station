"""Shared background task state and functions for all route modules.

All in-process progress dicts and async background functions live here to avoid
circular imports between the split route files.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from ..config.settings import Settings
from ..database.engine import build_engine, build_session_factory, get_session
from ..database.repository import PaperRepository, SummaryRepository
from ..models.paper import (
    CACHE_EMBEDDINGS,
    CACHE_FULLTEXT,
    CACHE_PDF,
    CACHE_SUMMARY,
    CitationORM,
    PaperEdgeORM,
    PaperORM,
)
from ..processing.embedding_service import get_embedding_service
from ..processing.entity_extractor import EntityExtractor
from ..processing.llm.factory import create_llm_client
from ..processing.summarizer import PaperSummarizer

logger = logging.getLogger(__name__)

# ── Shared progress registries ────────────────────────────────────────────────

_ocr_progress: dict[str, dict] = {}
_summarise_progress: dict[str, dict] = {}

_batch_state: dict = {
    "running": False,
    "action": None,
    "total": 0,
    "done": 0,
    "errors": 0,
    "current": None,
}

_classify_state: dict = {
    "running": False,
    "total": 0,
    "done": 0,
    "errors": 0,
    "cancel": False,
    "embedded_count": 0,
    "total_count": 0,
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_session(settings: Settings):
    engine = build_engine(settings.database.sqlite_path)
    return build_session_factory(engine)


def _build_ocr_backend(settings: Settings):
    """Instantiate the configured OCR backend, or return None if unavailable."""
    try:
        ocr_cfg = settings.ocr
        provider = (ocr_cfg.provider or settings.llm.provider).lower()
        model = ocr_cfg.model_name or settings.llm.model_name

        if provider == "ollama":
            backend_choice = ocr_cfg.backend.lower()
            if backend_choice == "auto":
                backend_choice = "qwen" if "qwen" in model.lower() else "vision"
            base_url = ocr_cfg.ollama_base_url or settings.llm.ollama_base_url
            if backend_choice == "qwen":
                from ..processing.pdf_ocr import QwenVLOCRBackend

                return QwenVLOCRBackend(
                    base_url=base_url,
                    model=model,
                    semaphore_limit=ocr_cfg.semaphore_limit,
                    max_tokens=ocr_cfg.max_tokens,
                )
            else:
                from ..processing.llm.ollama import OllamaClient
                from ..processing.pdf_ocr import VisionLLMOCR

                ollama_client = OllamaClient(
                    base_url=base_url,
                    model=model,
                    max_tokens=ocr_cfg.max_tokens,
                    temperature=0.05,
                )
                return VisionLLMOCR(ollama_client)

        elif provider == "vllm":
            from ..processing.pdf_ocr import DeepSeekOCRBackend, NanonetsOCRBackend

            backend_choice = ocr_cfg.backend.lower()
            if backend_choice == "auto":
                backend_choice = "nanonets" if "nanonets" in model.lower() else "deepseek"
            if backend_choice == "nanonets":
                return NanonetsOCRBackend(
                    base_url=ocr_cfg.vllm_base_url,
                    model=model,
                    semaphore_limit=ocr_cfg.semaphore_limit,
                    max_tokens=ocr_cfg.max_tokens,
                    repetition_penalty=ocr_cfg.repetition_penalty,
                )
            else:
                return DeepSeekOCRBackend(
                    base_url=ocr_cfg.vllm_base_url,
                    model=model,
                    semaphore_limit=ocr_cfg.semaphore_limit,
                    max_tokens=ocr_cfg.max_tokens,
                    use_ngram_processor=ocr_cfg.use_ngram_processor,
                )
    except Exception as exc:
        logger.warning("_build_ocr_backend: failed to instantiate backend: %s", exc)
    return None


async def _save_entities(factory, result) -> None:
    """Persist EntityExtractionResult to paper_entities and entity_relationships tables."""
    from ..models.entity import EntityRelationshipORM as _RelORM
    from ..models.entity import PaperEntityORM as _EntityORM

    if not result.entities:
        return

    with get_session(factory) as db:
        existing = (
            db.execute(select(_EntityORM).where(_EntityORM.paper_id == result.paper_id))
            .scalars()
            .all()
        )
        for e in existing:
            db.delete(e)
        db.flush()

        name_to_id: dict[str, int] = {}
        for ent in result.entities:
            orm_ent = _EntityORM(
                paper_id=result.paper_id,
                name=ent.name,
                entity_type=ent.entity_type,
                attributes_json=json.dumps(ent.attributes),
                model_used=result.model_used,
            )
            db.add(orm_ent)
            db.flush()
            name_to_id[ent.name] = orm_ent.id

        for rel in result.relationships:
            from_id = name_to_id.get(rel.from_entity)
            to_id = name_to_id.get(rel.to_entity)
            if from_id is None or to_id is None:
                continue
            db.add(
                _RelORM(
                    from_entity_id=from_id,
                    to_entity_id=to_id,
                    relationship_type=rel.relationship_type,
                    description=rel.description or None,
                    confidence=rel.confidence,
                    source_paper_id=result.paper_id,
                )
            )


# ── Background tasks ──────────────────────────────────────────────────────────


async def _bg_summarise(paper_id: str, settings: Settings) -> None:
    from ..processing.pdf_ocr import PDFOCRPipeline

    factory = _make_session(settings)
    with get_session(factory) as db:
        orm = db.get(PaperORM, paper_id)
        if orm is None:
            return
        paper = orm.to_pydantic()

    full_text: str | None = None
    pdf_path_for_lookup = Path(paper.local_pdf_path) if paper.local_pdf_path else Path(".")
    full_text = PDFOCRPipeline.load_text(pdf_path_for_lookup, paper_id, ocr_dir=settings.ocr_dir)
    if full_text:
        logger.info("Using full text for summarisation of %s (%d chars)", paper_id, len(full_text))

    logger.info("Background summarisation started for %s", paper_id)
    _summarise_progress[paper_id] = {"chunks_done": 0, "chunks_total": 0, "stage": "summarising"}

    def _on_chunk(done: int, total: int) -> None:
        _summarise_progress[paper_id] = {
            "chunks_done": done,
            "chunks_total": total,
            "stage": "map_reduce",
        }

    try:
        client = create_llm_client(settings)
        summarizer = PaperSummarizer(client, enable_thinking=settings.llm.enable_thinking)
        summary = await summarizer.summarize(paper, full_text=full_text, on_chunk=_on_chunk)

        with get_session(factory) as db:
            SummaryRepository(db).save(summary)
            orm = db.get(PaperORM, paper_id)
            if orm:
                orm.cache_flags = (orm.cache_flags or 0) | CACHE_SUMMARY
                if orm.status not in ("paywalled", "failed"):
                    orm.status = "summarized"
        _summarise_progress.pop(paper_id, None)
        logger.info(
            "Summarisation complete for %s (%s tokens)",
            paper_id,
            (summary.prompt_tokens or 0) + (summary.completion_tokens or 0),
        )

        try:
            _summarise_progress[paper_id] = {"stage": "extracting_entities"}
            content = full_text or paper.abstract or ""
            extractor = EntityExtractor(client)
            result = await extractor.extract(paper_id, paper.title, content)
            await _save_entities(factory, result)
            logger.info(
                "Entity extraction complete for %s: %d entities, %d relationships",
                paper_id,
                len(result.entities),
                len(result.relationships),
            )
        except Exception:
            logger.exception(
                "Entity extraction failed for %s (summarisation still saved)", paper_id
            )
        finally:
            _summarise_progress.pop(paper_id, None)
    except Exception:
        logger.exception("Summarisation failed for %s", paper_id)


async def _bg_download_pdf(paper_id: str, settings: Settings) -> None:
    from ..ingestion.pdf_downloader import PDFDownloader

    factory = _make_session(settings)
    with get_session(factory) as db:
        orm = db.get(PaperORM, paper_id)
        if orm is None:
            return
        paper = orm.to_pydantic()

    pdf_url = paper.pdf_url
    if not pdf_url and paper.arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{paper.arxiv_id}"
    if not pdf_url:
        logger.warning("No PDF URL for %s — marking failed", paper_id)
        with get_session(factory) as db:
            orm = db.get(PaperORM, paper_id)
            if orm:
                orm.status = "failed"
        return

    logger.info("Downloading PDF for %s from %s", paper_id, pdf_url)
    try:
        downloader = PDFDownloader(settings.papers_dir)
        loop = asyncio.get_event_loop()
        local_path = await loop.run_in_executor(None, downloader.download, pdf_url, paper_id)
        if local_path:
            with get_session(factory) as db:
                PaperRepository(db).mark_downloaded(paper_id, str(local_path))
                orm = db.get(PaperORM, paper_id)
                if orm:
                    orm.cache_flags = (orm.cache_flags or 0) | CACHE_PDF
            logger.info("PDF saved for %s → %s", paper_id, local_path)
        else:
            # Downloader returned None — typically a 403/paywalled response
            logger.warning("PDF download returned no file for %s — marking failed", paper_id)
            with get_session(factory) as db:
                orm = db.get(PaperORM, paper_id)
                if orm and orm.status not in ("paywalled", "summarized"):
                    orm.status = "failed"
    except Exception:
        logger.exception("PDF download failed for %s", paper_id)
        with get_session(factory) as db:
            orm = db.get(PaperORM, paper_id)
            if orm and orm.status not in ("paywalled", "summarized"):
                orm.status = "failed"


async def _bg_ocr(paper_id: str, settings: Settings, auto_summarise: bool = True) -> None:
    from ..processing.pdf_ocr import PDFOCRPipeline, PDFPageRenderer

    factory = _make_session(settings)
    with get_session(factory) as db:
        orm = db.get(PaperORM, paper_id)
        if orm is None:
            return
        if not orm.local_pdf_path:
            logger.warning("OCR requested for %s but PDF not downloaded", paper_id)
            return
        pdf_path = Path(orm.local_pdf_path)
        if not pdf_path.exists():
            logger.warning("OCR requested for %s but PDF file missing: %s", paper_id, pdf_path)
            return
        paper = orm.to_pydantic()

    ocr_cfg = settings.ocr
    provider = (ocr_cfg.provider or settings.llm.provider).lower()
    model = ocr_cfg.model_name or settings.llm.model_name

    if provider == "ollama":
        base_url = ocr_cfg.ollama_base_url or settings.llm.ollama_base_url
        backend_choice = ocr_cfg.backend.lower()
        if backend_choice == "auto":
            backend_choice = "qwen" if "qwen" in model.lower() else "vision"

        if backend_choice == "qwen":
            from ..processing.pdf_ocr import QwenVLOCRBackend

            backend = QwenVLOCRBackend(
                base_url=base_url,
                model=model,
                semaphore_limit=ocr_cfg.semaphore_limit,
                max_tokens=ocr_cfg.max_tokens,
            )
        else:
            from ..processing.llm.ollama import OllamaClient
            from ..processing.pdf_ocr import VisionLLMOCR

            ollama_client = OllamaClient(
                base_url=base_url,
                model=model,
                max_tokens=ocr_cfg.max_tokens,
                temperature=0.05,
            )
            backend = VisionLLMOCR(ollama_client)
        renderer = PDFPageRenderer(dpi=ocr_cfg.dpi)
    elif provider == "vllm":
        from ..processing.pdf_ocr import DeepSeekOCRBackend, NanonetsOCRBackend

        backend_choice = ocr_cfg.backend.lower()
        if backend_choice == "auto":
            model_lower = model.lower()
            backend_choice = "nanonets" if "nanonets" in model_lower else "deepseek"

        if backend_choice == "nanonets":
            backend = NanonetsOCRBackend(
                base_url=ocr_cfg.vllm_base_url,
                model=model,
                semaphore_limit=ocr_cfg.semaphore_limit,
                max_tokens=ocr_cfg.max_tokens,
                repetition_penalty=ocr_cfg.repetition_penalty,
            )
            renderer = PDFPageRenderer(dpi=ocr_cfg.dpi, image_format="png")
        else:
            backend = DeepSeekOCRBackend(
                base_url=ocr_cfg.vllm_base_url,
                model=model,
                semaphore_limit=ocr_cfg.semaphore_limit,
                max_tokens=ocr_cfg.max_tokens,
                use_ngram_processor=ocr_cfg.use_ngram_processor,
            )
            renderer = PDFPageRenderer(dpi=ocr_cfg.dpi)
    else:
        logger.warning("OCR requires provider=ollama or vllm (current: %s)", provider)
        return

    ocr_dir = settings.ocr_dir
    ocr_dir.mkdir(parents=True, exist_ok=True)
    pipeline = PDFOCRPipeline(backend, renderer, ocr_dir=ocr_dir)

    _ocr_progress[paper_id] = {"running": True, "pages_done": 0, "pages_total": 0}

    def _on_progress(done: int, total: int) -> None:
        _ocr_progress[paper_id] = {"running": True, "pages_done": done, "pages_total": total}

    try:
        text = await pipeline.run(pdf_path, paper, on_progress=_on_progress)
        if text:
            from ..processing.pdf_ocr import PDFOCRPipeline as _Pipeline

            meta_path = ocr_dir / f"{_Pipeline.safe_name(paper_id)}_meta.json"
            meta_payload = {
                "model": model,
                "provider": provider,
                "completed_at": datetime.utcnow().isoformat(),
            }
            meta_path.write_text(json.dumps(meta_payload))
            with get_session(factory) as db:
                orm = db.get(PaperORM, paper_id)
                if orm:
                    orm.cache_flags = (orm.cache_flags or 0) | CACHE_FULLTEXT
            llm = settings.llm
            ocr_model_name = ocr_cfg.model_name or llm.model_name
            same_vllm_ocr_model = (
                provider == "vllm"
                and llm.provider.lower() == "vllm"
                and llm.model_name == ocr_model_name
            )
            if same_vllm_ocr_model:
                logger.warning(
                    "Skipping auto-summarisation for %s: LLM__MODEL_NAME (%s) is "
                    "the same OCR-only model. Set LLM__PROVIDER + LLM__MODEL_NAME "
                    "to a chat model for summarisation.",
                    paper_id,
                    llm.model_name,
                )
            elif auto_summarise:
                logger.info("OCR complete for %s — auto-chaining summarisation", paper_id)
                await _bg_summarise(paper_id, settings)
    except Exception:
        logger.exception("OCR pipeline failed for %s", paper_id)
    finally:
        prog = _ocr_progress.get(paper_id, {})
        _ocr_progress[paper_id] = {**prog, "running": False}


async def _bg_extract_entities(paper_id: str, settings: Settings) -> None:
    from ..processing.pdf_ocr import PDFOCRPipeline

    factory = _make_session(settings)
    with get_session(factory) as db:
        orm = db.get(PaperORM, paper_id)
        if orm is None:
            return
        paper = orm.to_pydantic()

    pdf_path_for_lookup = Path(paper.local_pdf_path) if paper.local_pdf_path else Path(".")
    full_text = PDFOCRPipeline.load_text(pdf_path_for_lookup, paper_id, ocr_dir=settings.ocr_dir)
    content = full_text or paper.abstract or ""

    try:
        client = create_llm_client(settings)
        extractor = EntityExtractor(client)
        result = await extractor.extract(paper_id, paper.title, content)
        await _save_entities(factory, result)
        logger.info("Standalone entity extraction complete for %s", paper_id)
    except Exception:
        logger.exception("Standalone entity extraction failed for %s", paper_id)


def _bg_embed(paper_id: str, settings: Settings) -> None:
    """Embed paper into ChromaDB (abstract for graph, chunks for RAG)."""
    try:
        engine = build_engine(settings.database.sqlite_path)
        factory = build_session_factory(engine)
        with get_session(factory) as db:
            orm = db.get(PaperORM, paper_id)
            if orm is None:
                return
            abstract_text = orm.abstract or orm.title or ""

        svc = get_embedding_service()
        svc.embed_paper(paper_id, abstract_text)

        with get_session(factory) as db2:
            orm2 = db2.get(PaperORM, paper_id)
            if orm2:
                orm2.cache_flags = (orm2.cache_flags or 0) | CACHE_EMBEDDINGS
    except Exception as exc:
        logger.error("_bg_embed %s failed: %s", paper_id, exc)


def _bg_embed_batch(settings: Settings) -> None:
    """Embed all unembedded papers."""
    try:
        engine = build_engine(settings.database.sqlite_path)
        factory = build_session_factory(engine)
        with get_session(factory) as db:
            rows = db.execute(select(PaperORM.id, PaperORM.cache_flags)).fetchall()
        paper_ids = [r[0] for r in rows if not ((r[1] or 0) & CACHE_EMBEDDINGS)]
        for paper_id in paper_ids:
            _bg_embed(paper_id, settings)
    except Exception as exc:
        logger.error("_bg_embed_batch failed: %s", exc)


async def _bg_pdf_extract(paper_id: str, settings: Settings, auto_summarise: bool = True) -> None:
    """Extract text from a PDF using PyMuPDF (no vision LLM required)."""
    try:
        import pymupdf  # type: ignore
    except ImportError:
        logger.error("PyMuPDF not installed. Run: pip install pymupdf")
        return

    from ..processing.pdf_ocr import PDFOCRPipeline

    factory = _make_session(settings)
    with get_session(factory) as db:
        orm = db.get(PaperORM, paper_id)
        if orm is None:
            return
        if not orm.local_pdf_path:
            logger.warning("PDF extract requested for %s but PDF not downloaded", paper_id)
            return
        pdf_path = Path(orm.local_pdf_path)
        if not pdf_path.exists():
            logger.warning("PDF extract requested for %s but file missing: %s", paper_id, pdf_path)
            return

    try:
        doc = pymupdf.open(str(pdf_path))
        pages: list[str] = []
        for i, page in enumerate(doc, 1):
            text = page.get_text("text").strip()
            if text:
                pages.append(f"--- Page {i} ---\n{text}")
        doc.close()
        full_text = "\n\n".join(pages)
        if not full_text.strip():
            logger.warning("PDF extract produced no text for %s", paper_id)
            return
    except Exception:
        logger.exception("PDF extract failed for %s", paper_id)
        return

    ocr_dir = settings.ocr_dir
    ocr_dir.mkdir(parents=True, exist_ok=True)
    safe = PDFOCRPipeline.safe_name(paper_id)
    (ocr_dir / f"{safe}.txt").write_text(full_text)
    meta_payload = {
        "model": "pdf-extract",
        "provider": "pymupdf",
        "completed_at": datetime.utcnow().isoformat(),
    }
    (ocr_dir / f"{safe}_meta.json").write_text(json.dumps(meta_payload))

    factory2 = _make_session(settings)
    with get_session(factory2) as db2:
        orm2 = db2.get(PaperORM, paper_id)
        if orm2:
            orm2.cache_flags = (orm2.cache_flags or 0) | CACHE_FULLTEXT
            db2.flush()

    logger.info("PDF extract complete for %s — %d chars", paper_id, len(full_text))

    if auto_summarise:
        await _bg_summarise(paper_id, settings)

    _ocr_progress[paper_id] = {"running": False, "done": 0, "total": 0}


async def _bg_batch(paper_ids: list[str], action: str, settings: Settings) -> None:
    _batch_state.update(
        running=True, action=action, total=len(paper_ids), done=0, errors=0, current=None
    )
    try:
        for paper_id in paper_ids:
            _batch_state["current"] = paper_id
            try:
                if action == "ocr":
                    await _bg_ocr(paper_id, settings, auto_summarise=False)
                elif action == "ocr_summarize":
                    await _bg_ocr(paper_id, settings, auto_summarise=False)
                    await _bg_summarise(paper_id, settings)
                elif action == "summarize":
                    await _bg_summarise(paper_id, settings)
                elif action == "extract":
                    await _bg_pdf_extract(paper_id, settings, auto_summarise=False)
                elif action == "extract_summarize":
                    await _bg_pdf_extract(paper_id, settings, auto_summarise=False)
                    await _bg_summarise(paper_id, settings)
                elif action == "embed":
                    _bg_embed(paper_id, settings)
                elif action == "download_pdf":
                    await _bg_download_pdf(paper_id, settings)
            except Exception:
                logger.exception("Batch job error on %s", paper_id)
                _batch_state["errors"] += 1
            _batch_state["done"] += 1
    finally:
        _batch_state.update(running=False, current=None)


async def _bg_classify_edges(settings: Settings, neighbors: int, all_sources: bool = False) -> None:
    from ..processing.llm.base import Message
    from ..processing.prompts import render as render_prompt

    _classify_state.update(
        running=True, total=0, done=0, errors=0, cancel=False, embedded_count=0, total_count=0
    )
    try:
        svc = get_embedding_service()
        engine = build_engine(settings.database.sqlite_path)
        factory = build_session_factory(engine)

        with get_session(factory) as db:
            all_papers = db.execute(select(PaperORM)).scalars().all()
            paper_map = {p.id: {"title": p.title, "abstract": p.abstract} for p in all_papers}
            embedded_ids = [p.id for p in all_papers if (p.cache_flags or 0) & CACHE_EMBEDDINGS]
            existing = {
                (min(r.from_id, r.to_id), max(r.from_id, r.to_id))
                for r in db.execute(select(PaperEdgeORM)).scalars().all()
            }
            citation_pairs: set[tuple[str, str]] = set()
            if all_sources:
                for c in db.execute(select(CitationORM)).scalars().all():
                    a, b = c.citing_paper_id, c.cited_paper_id
                    key = (min(a, b), max(a, b))
                    if key not in existing and a in paper_map and b in paper_map:
                        citation_pairs.add(key)

        embedded_set = set(embedded_ids)
        _classify_state["embedded_count"] = len(embedded_ids)
        _classify_state["total_count"] = len(paper_map)

        def _norm(a: str, b: str) -> tuple[str, str]:
            return (a, b) if a < b else (b, a)

        sim_map: dict[tuple[str, str], float] = {}
        pairs_set: set[tuple[str, str]] = set()

        if len(embedded_ids) >= 2:
            sem_edges = svc.get_semantic_edges(embedded_ids, threshold=-1.0, k=neighbors)
            for e in sem_edges:
                key = _norm(e["from"], e["to"])
                sim_map[key] = e["similarity"]
                if key not in existing:
                    pairs_set.add(key)

        if all_sources:
            unembedded = {
                pid: f"{d['title']} {d['abstract'] or ''}".strip()
                for pid, d in paper_map.items()
                if pid not in embedded_set and (d["title"] or d["abstract"])
            }
            if unembedded and len(embedded_ids) >= 1:
                extra = svc.get_neighbors_for_unembedded(unembedded, k=neighbors, exclude_ids=None)
                for e in extra:
                    key = _norm(e["from"], e["to"])
                    sim_map[key] = e["similarity"]
                    if key not in existing:
                        pairs_set.add(key)
            pairs_set.update(citation_pairs)

        pairs = list(pairs_set)
        _classify_state["total"] = len(pairs)
        logger.info(
            "Classifying %d pairs (all_sources=%s, embedded=%d/%d)",
            len(pairs),
            all_sources,
            len(embedded_ids),
            len(paper_map),
        )

        client = create_llm_client(settings)
        for from_id, to_id in pairs:
            if _classify_state["cancel"]:
                logger.info(
                    "Classification cancelled after %d/%d pairs",
                    _classify_state["done"],
                    len(pairs),
                )
                break
            _classify_state["done"] += 1
            pa = paper_map.get(from_id)
            pb = paper_map.get(to_id)
            if not pa or not pb:
                continue
            try:
                prompt = render_prompt(
                    "edge_classify",
                    title_a=pa["title"],
                    abstract_a=(pa["abstract"] or "")[:400],
                    title_b=pb["title"],
                    abstract_b=(pb["abstract"] or "")[:400],
                )
                resp = await client.chat(
                    [Message(role="user", content=prompt)],
                    system_prompt="You classify relationships between ML research papers. Be precise and concise.",
                    max_tokens=100,
                )
                text = (resp.content or "").strip()
                _VALID_TYPES = {
                    "extends",
                    "supersedes",
                    "challenges",
                    "applies",
                    "uses",
                    "surveys",
                    "baseline",
                    "concurrent",
                    "unrelated",
                }
                edge_type = "concurrent"
                description = None
                llm_confidence: float | None = None
                for line in text.splitlines():
                    upper = line.upper()
                    if upper.startswith("TYPE:"):
                        raw = line.split(":", 1)[1].strip().lower()
                        if raw in _VALID_TYPES:
                            edge_type = raw
                    elif upper.startswith("LINK:"):
                        description = line.split(":", 1)[1].strip()
                    elif upper.startswith("CONF:"):
                        try:
                            llm_confidence = max(
                                0.0, min(1.0, float(line.split(":", 1)[1].strip()))
                            )
                        except ValueError:
                            pass

                if edge_type == "unrelated":
                    continue

                similarity = sim_map.get((from_id, to_id), 0.8)
                confidence = llm_confidence if llm_confidence is not None else round(similarity, 4)
                with get_session(factory) as db:
                    db.add(
                        PaperEdgeORM(
                            from_id=from_id,
                            to_id=to_id,
                            edge_type=edge_type,
                            description=description,
                            confidence=round(confidence, 4),
                            source="llm",
                        )
                    )
            except Exception as exc:
                logger.warning("classify_edges pair (%s, %s): %s", from_id, to_id, exc)
                _classify_state["errors"] += 1
    except Exception as exc:
        logger.exception("_bg_classify_edges failed: %s", exc)
    finally:
        _classify_state["running"] = False
        logger.info(
            "Classification done: %d pairs, %d errors",
            _classify_state["done"],
            _classify_state["errors"],
        )
