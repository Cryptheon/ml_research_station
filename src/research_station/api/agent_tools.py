"""Agent tool implementations for the OpenAI Agents SDK loop.

These are the same tools exposed to OpenCode via the MCP server,
re-implemented here as plain sync Python functions and wrapped with
@function_tool for the OpenAI Agents SDK.  No FastMCP decorators.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]  # src/research_station/api/ → project root

# Per-request paper context — set by extras.py before each agent run so tools
# can scope their output to the active paper without needing an extra parameter.
import contextvars as _cv

_active_paper_id: _cv.ContextVar[str | None] = _cv.ContextVar("active_paper_id", default=None)


def set_active_paper(paper_id: str | None) -> None:
    _active_paper_id.set(paper_id)


def _get_active_paper() -> str | None:
    return _active_paper_id.get()


# ── Lazy singletons ────────────────────────────────────────────────────────────

_session_factory = None
_embed_svc = None


def _db():
    global _session_factory
    if _session_factory is None:
        from ..config.settings import get_settings
        from ..database.engine import build_engine, build_session_factory

        s = get_settings()
        _session_factory = build_session_factory(build_engine(s.database.sqlite_path))
    from ..database.engine import get_session

    return get_session(_session_factory)


def _embed():
    global _embed_svc
    if _embed_svc is None:
        from ..processing.embedding_service import get_embedding_service

        _embed_svc = get_embedding_service()
    return _embed_svc


def _cfg():
    from ..config.settings import get_settings

    return get_settings()


def _api_base() -> str:
    return os.environ.get("RS_API_BASE", "http://localhost:8080")


def _http(path: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{_api_base()}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _fmt_paper(p, include_abstract=True, include_summary=False, summary_orm=None) -> list[str]:
    def _author_name(a) -> str:
        if isinstance(a, str):
            return a
        if isinstance(a, dict):
            return a.get("name", "")
        return getattr(a, "name", "") or ""

    authors = ", ".join(_author_name(a) for a in (p.authors or []))
    lines = [
        f"ID:      {p.id}",
        f"Title:   {p.title}",
        f"Authors: {authors}",
        f"Source:  {p.source}  Venue: {p.venue or 'n/a'}  Date: {str(p.published_date or '')[:10]}",
        f"Cited:   {p.citation_count or 0}   Refs: {p.reference_count or 0}",
        f"Topics:  {', '.join(p.topics or [])}",
    ]
    if include_abstract and p.abstract:
        lines += ["", "Abstract:", p.abstract]
    if include_summary and summary_orm:
        s = summary_orm.to_pydantic()
        lines += [
            "",
            "── LLM Summary ─────────────────────────────────────────",
            f"TL;DR: {s.tldr}",
            "",
            "Methodology:",
            s.methodology,
            "",
            "Contributions:",
            *[f"  • {c}" for c in s.contributions],
            "",
            "Key results:",
            *[f"  • {r}" for r in s.key_results],
            "",
            "Limitations:",
            *[f"  • {l}" for l in s.limitations],
        ]
    return lines


# ═══════════════════════════════════════════════════════════════════════════════
# Tool implementations
# ═══════════════════════════════════════════════════════════════════════════════


def search_papers(
    query: str, limit: int = 10, since_days: str | int | None = None, source: str | None = None
) -> str:
    # Coerce "None" / "" strings that models sometimes emit
    if isinstance(since_days, str):
        s = since_days.strip()
        since_days = int(s) if s.lstrip("-").isdigit() else None
    if isinstance(source, str) and source.lower() in ("none", "null", ""):
        source = None
    try:
        with _db() as db:
            from ..database.repository import PaperRepository

            papers = PaperRepository(db).search(
                query=query,
                sources=[source] if source else None,
                since_days=since_days,
                limit=min(limit, 100),
            )
        if not papers:
            return f"No papers found matching '{query}'."
        lines = [f"Found {len(papers)} papers for '{query}':\n"]
        for i, p in enumerate(papers, 1):
            lines.append(f"{i:3}. [{p.id}]")
            lines.append(f"     {p.title}")
            lines.append(
                f"     {p.source} · {p.venue or 'n/a'} · "
                f"{str(p.published_date or '')[:10]} · {p.citation_count or 0} citations"
            )
            if p.abstract:
                lines.append(f"     {p.abstract[:180]}…")
            lines.append("")
        return "\n".join(lines)
    except Exception as exc:
        log.exception("search_papers")
        return f"Error: {exc}"


def semantic_search(query: str, limit: int = 8) -> str:
    try:
        svc = _embed()
        count = svc._col.count()
        if count == 0:
            return "No embedded papers found. Run 'Embed corpus' first in the Graph tab."
        # Embed the query via the EF directly (avoids embed_query interface issues)
        try:
            raw_vec = svc._ef([query])[0]
        except Exception as embed_err:
            # Embedding server (vLLM/Ollama) is not reachable
            return (
                f"Embedding server unreachable — cannot run semantic search. "
                f"({type(embed_err).__name__}: {embed_err}). "
                f"Use search_papers() for keyword search instead."
            )
        query_vec = raw_vec.tolist() if hasattr(raw_vec, "tolist") else list(raw_vec)
        results = svc._col.query(
            query_embeddings=[query_vec],
            n_results=min(limit, count),
            include=["distances"],
        )
        ids = results["ids"][0]
        dists = results["distances"][0]
        if not ids:
            return (
                f"No results returned from embedding search ({count} papers embedded). "
                "The query may not semantically match anything in the corpus — try search_papers() instead."
            )
        with _db() as db:
            from ..models.paper import PaperORM

            lines = [f"Top {len(ids)} papers semantically similar to: '{query}'\n"]
            for i, (pid, dist) in enumerate(zip(ids, dists), 1):
                sim = round(1.0 - float(dist), 3)
                orm = db.get(PaperORM, pid)
                title = orm.title if orm else pid
                abstract = (orm.abstract or "")[:180] if orm else ""
                venue = (orm.venue or orm.source or "") if orm else ""
                lines.append(f"{i:2}. [{pid}]  similarity={sim}")
                lines.append(f"    {title}")
                lines.append(f"    {venue}  {abstract}…")
                lines.append("")
        return "\n".join(lines)
    except Exception as exc:
        log.exception("semantic_search")
        return f"Error: {exc}"


def find_similar_papers(paper_id: str, k: int = 10) -> str:
    try:
        svc = _embed()
        neighbors = svc.get_neighbors(paper_id, k=k)
        if not neighbors:
            return (
                f"No neighbours found for '{paper_id}'. "
                "The paper may not be embedded — use Graph tab → Embed corpus."
            )
        with _db() as db:
            from ..models.paper import PaperORM

            lines = [f"Top {len(neighbors)} papers similar to [{paper_id}]:\n"]
            for i, nb in enumerate(neighbors, 1):
                orm = db.get(PaperORM, nb["id"])
                title = orm.title if orm else nb["id"]
                venue = (orm.venue or orm.source or "") if orm else ""
                date = str(orm.published_date or "")[:10] if orm else ""
                lines.append(f"{i:2}. [{nb['id']}]  similarity={nb['similarity']}")
                lines.append(f"    {title}")
                lines.append(f"    {venue}  {date}")
                lines.append("")
        return "\n".join(lines)
    except Exception as exc:
        log.exception("find_similar_papers")
        return f"Error: {exc}"


def _auto_ingest(paper_id: str) -> bool:
    """Fetch an arXiv paper by ID, store it, and queue PDF download + embedding.

    Returns True on success, False if the ID isn't an arXiv ID.
    Raises on fetch / storage errors so the caller can surface them.
    """
    if not paper_id.startswith("arxiv:"):
        return False

    import re as _re

    import arxiv as arxiv_lib

    from ..database.repository import CitationRepository, PaperRepository
    from ..ingestion.arxiv_fetcher import ArxivFetcher
    from ..ingestion.openalex_enricher import OpenAlexClient
    from ..ingestion.semantic_scholar import SemanticScholarClient
    from ..models.taxonomy import classify as classify_topics

    arxiv_id = paper_id[len("arxiv:") :].split("v")[0]
    # Validate: arXiv IDs look like YYMM.NNNNN or category/NNNNNNN
    if not _re.match(r"^(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})$", arxiv_id):
        log.warning("auto_ingest: '%s' is not a valid arXiv ID format, skipping", arxiv_id)
        return False
    s = _cfg()

    log.info("auto_ingest: fetching %s from arXiv", arxiv_id)
    client = arxiv_lib.Client(page_size=1, delay_seconds=1, num_retries=3)
    results = list(client.results(arxiv_lib.Search(id_list=[arxiv_id])))
    if not results:
        raise ValueError(f"arXiv paper not found: {arxiv_id}")

    fetcher = ArxivFetcher(s.rate_limits)
    paper = fetcher._entry_to_paper(results[0])
    if not paper.topics:
        paper.topics = classify_topics(paper.title, paper.abstract)

    # Best-effort enrichment (citation counts, DOI, references)
    openalex = OpenAlexClient(delay_seconds=0.12)
    try:
        if s.semantic_scholar_api_key:
            s2 = SemanticScholarClient(s.semantic_scholar_api_key, s.rate_limits)
            paper = s2.enrich_paper(paper)
        paper = openalex.enrich_paper(paper)
    except Exception as e:
        log.warning("auto_ingest: enrichment failed for %s (non-fatal): %s", paper_id, e)

    with _db() as db:
        PaperRepository(db).upsert_many([paper])
        # Store citation edges
        try:
            if paper.semantic_scholar_id and s.semantic_scholar_api_key:
                s2 = SemanticScholarClient(s.semantic_scholar_api_key, s.rate_limits)
                refs = s2.get_references(paper)
            else:
                refs = openalex.get_references(paper)
            CitationRepository(db).upsert_many(refs)
        except Exception as e:
            log.warning("auto_ingest: citation fetch failed for %s (non-fatal): %s", paper_id, e)
        # Note: agent-fetched papers are intentionally NOT added to ManuallyAddedPaperORM
        # so the graph "Show manual" toggle only highlights papers the user added themselves.

    log.info("auto_ingest: stored %s (%s)", paper_id, paper.title)

    # Trigger PDF download + embedding via the server's background-task endpoints
    enc = urllib.parse.quote(paper_id, safe="")
    for path in (f"/papers/{enc}/ingest", f"/papers/{enc}/embed"):
        try:
            _http(path, method="POST")
        except Exception as e:
            log.warning("auto_ingest: trigger %s failed (non-fatal): %s", path, e)

    return True


def get_paper(paper_id: str, include_summary: bool = True) -> str:
    """Retrieve full metadata, abstract, and LLM-generated summary for a paper.

    If the paper is not yet in the local database and the ID is an arXiv ID
    (e.g. 'arxiv:2301.00001'), it will be fetched from arXiv, stored, and its
    PDF queued for download automatically before returning the metadata.

    Args:
        paper_id:        Paper ID, e.g. 'arxiv:2301.00001'.
        include_summary: Include LLM summary if available (default true).
    """
    try:
        from sqlalchemy import desc, select

        from ..models.paper import PaperORM
        from ..models.summary import PaperSummaryORM

        # Check DB first; if missing, attempt auto-ingest from arXiv
        with _db() as db:
            exists = db.get(PaperORM, paper_id) is not None

        if not exists:
            found = _auto_ingest(paper_id)
            if not found:
                return f"Paper '{paper_id}' not found in database."

        with _db() as db:
            orm = db.get(PaperORM, paper_id)
            if orm is None:
                return f"Paper '{paper_id}' was ingested but could not be retrieved."
            summary_orm = None
            if include_summary:
                stmt = (
                    select(PaperSummaryORM)
                    .where(PaperSummaryORM.paper_id == paper_id)
                    .order_by(desc(PaperSummaryORM.generated_at))
                    .limit(1)
                )
                summary_orm = db.execute(stmt).scalar_one_or_none()
            lines = _fmt_paper(
                orm.to_pydantic(),
                include_abstract=True,
                include_summary=include_summary,
                summary_orm=summary_orm,
            )
            if include_summary and summary_orm is None:
                lines += ["", "(No LLM summary yet. Call summarize_paper() to generate one.)"]
            has_pdf = bool(orm.local_pdf_path)
            paper_status = orm.status or ""
            paper_source = orm.source or ""

        if not has_pdf:
            if paper_status == "paywalled":
                lines += [
                    "",
                    "⚠ PDF unavailable — this paper is paywalled. Full text cannot be retrieved. "
                    "Use the abstract and summary only. Do not retry PDF download.",
                ]
            elif paper_status == "failed":
                lines += [
                    "",
                    "⚠ PDF download failed permanently. Full text is unavailable. "
                    "Do not retry — use summarize_paper() on the abstract instead.",
                ]
            elif paper_source in ("wikipedia", "web"):
                lines += [
                    "",
                    "(No PDF — this is a web/Wikipedia paper. "
                    "Full text is in the OCR cache; use rag_query() or extract_pdf_text().)",
                ]
            else:
                lines += [
                    "",
                    "⟳ PDF download in progress — call get_paper() once more after ~30 s to confirm. "
                    "If the PDF is still missing after one retry, assume it is unavailable.",
                ]

        # Record agent view (silent on failure)
        try:
            from ..models.paper_view import PaperViewORM

            with _db() as db:
                db.add(PaperViewORM(paper_id=paper_id, viewer="agent"))
        except Exception:
            pass

        return "\n".join(lines)
    except Exception as exc:
        log.exception("get_paper")
        return f"Error: {exc}"


def list_papers(
    limit: int = 20, source: str | None = None, since_days: str | int | None = None
) -> str:
    # Tolerate the model passing the string "None" / "null" for optional params
    if isinstance(since_days, str):
        s = since_days.strip()
        since_days = int(s) if s.lstrip("-").isdigit() else None
    if isinstance(source, str) and source.lower() in ("none", "null", ""):
        source = None
    elif isinstance(source, str):
        source = source.lower()  # normalise e.g. "arXiv" → "arxiv"
    try:
        with _db() as db:
            from ..database.repository import PaperRepository

            papers = PaperRepository(db).search(
                query="",
                sources=[source] if source else None,
                since_days=since_days,
                limit=min(limit, 2000),
            )
        if not papers:
            return "No papers found."
        lines = [f"{len(papers)} papers:\n"]
        for i, p in enumerate(papers, 1):
            lines.append(f"{i:3}. [{p.id}]  {str(p.published_date or '')[:10]}")
            lines.append(f"     {p.title}")
            lines.append(f"     {p.source}  {p.venue or ''}  {p.citation_count or 0} citations")
        return "\n".join(lines)
    except Exception as exc:
        log.exception("list_papers")
        return f"Error: {exc}"


def query_database(sql: str) -> str:
    stripped = sql.strip().lower().lstrip("(")
    if not (stripped.startswith("select") or stripped.startswith("with")):
        return "Error: only SELECT / WITH queries are permitted."
    try:
        import sqlite3

        s = _cfg()
        con = sqlite3.connect(f"file:{s.database.sqlite_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        cur = con.execute(sql)
        rows = cur.fetchmany(200)
        con.close()
        if not rows:
            return "Query returned 0 rows. (SQL executed successfully — the table is empty or the filter matched nothing.)"
        keys = list(rows[0].keys())
        sep = " | "
        header = sep.join(keys)
        body = "\n".join(sep.join(str(row[k] or "") for k in keys) for row in rows)
        return f"{len(rows)} rows:\n{header}\n{'-' * len(header)}\n{body}"
    except Exception as exc:
        return f"Error: {exc}"


_summarize_active: set[str] = set()  # paper IDs currently being summarised
_summarize_lock = threading.Lock()


def summarize_paper(paper_id: str) -> str:
    """Generate an LLM summary for a paper and wait until it is ready.

    Triggers summarisation, then blocks (polling every 5 s) until the summary
    is written to the database.  Waits as long as necessary — long papers with
    map-reduce summarisation can take many minutes.

    Do NOT call this again for the same paper while it is still running — the
    tool blocks until complete and a duplicate call will also block, wasting
    time and context.  If you see "already in progress" returned, simply wait
    and do not retry.

    Args:
        paper_id: The paper's ID (e.g. 'arxiv:2304.00001').
    """

    with _summarize_lock:
        if paper_id in _summarize_active:
            return (
                f"summarize_paper('{paper_id}') is already running in this session. "
                "Do NOT call it again — wait for the current run to finish and return its result."
            )
        _summarize_active.add(paper_id)

    try:
        return _summarize_paper_inner(paper_id)
    finally:
        with _summarize_lock:
            _summarize_active.discard(paper_id)


def _summarize_paper_inner(paper_id: str) -> str:
    import time

    from sqlalchemy import desc, select

    try:
        _http(f"/papers/{urllib.parse.quote(paper_id, safe='')}/reader/regenerate", method="POST")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode()).get("detail", exc.reason)
        except Exception:
            detail = exc.reason
        return f"API error {exc.code}: {detail}"
    except Exception as exc:
        return f"Error starting summarisation: {exc}"

    MAX_WAIT_S = 600  # 10 minutes wall-clock maximum
    start_ts = time.time()
    idle_streak = 0  # consecutive polls where active=False and no summary yet

    while True:
        time.sleep(5)
        elapsed = time.time() - start_ts

        if elapsed > MAX_WAIT_S:
            return (
                f"Summarisation of '{paper_id}' did not complete within "
                f"{MAX_WAIT_S}s. The job may still be running — check the server logs."
            )

        # Check progress endpoint
        active = False
        try:
            prog = _http(f"/papers/{urllib.parse.quote(paper_id, safe='')}/summarise/progress")
            active = bool(prog.get("active"))
        except Exception:
            pass

        if active:
            idle_streak = 0
            continue

        # Not active — check DB for summary
        try:
            with _db() as db:
                from ..models.summary import PaperSummaryORM

                stmt = (
                    select(PaperSummaryORM)
                    .where(PaperSummaryORM.paper_id == paper_id)
                    .order_by(desc(PaperSummaryORM.generated_at))
                    .limit(1)
                )
                summ = db.execute(stmt).scalar_one_or_none()
                if summ:
                    s = summ.to_pydantic()
                    contribs = "; ".join(s.contributions[:3]) if s.contributions else "—"
                    lines = [
                        f"Summary ready for '{paper_id}' (took ~{round(elapsed)}s).",
                        "",
                        f"**TL;DR:** {s.tldr}",
                        "",
                        f"**Key contributions:** {contribs}",
                        "",
                        f"**Methodology:** {(s.methodology or '')[:400]}",
                    ]
                    return "\n".join(lines)
        except Exception as exc:
            log.warning("summarize_paper DB check error: %s", exc)

        # Give up after 120 s of consecutive idle (24 × 5 s) — handles map-reduce gaps
        idle_streak += 1
        if idle_streak >= 24 and elapsed > 60:
            return (
                f"Summarisation of '{paper_id}' appears to have stopped after {round(elapsed)}s "
                "without producing a result. Check the server logs for errors."
            )


def embed_paper(paper_id: str) -> str:
    """Embed a paper into the vector store so it appears in semantic search and the citation graph.

    Embedding uses the paper's title and abstract. Run this after ingesting or downloading
    a new paper so find_similar_papers() and semantic_search() can find it.

    Args:
        paper_id: The paper's ID (e.g. 'arxiv:2304.00001').
    """
    try:
        _http(f"/papers/{urllib.parse.quote(paper_id, safe='')}/embed", method="POST")
        return (
            f"Embedding queued for '{paper_id}'. "
            "The paper will appear in semantic_search() and find_similar_papers() within a few seconds."
        )
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode()).get("detail", exc.reason)
        except Exception:
            detail = exc.reason
        return f"API error {exc.code}: {detail}"
    except Exception as exc:
        return f"Error: {exc}"


def ocr_paper(paper_id: str) -> str:
    try:
        _http(f"/papers/{urllib.parse.quote(paper_id, safe='')}/ocr", method="POST")
        return (
            f"OCR started for '{paper_id}'. This may take several minutes. "
            "Afterwards, rag_query(paper_id=...) will use the full text."
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            return "OCR is already running for this paper."
        try:
            detail = json.loads(exc.read().decode()).get("detail", exc.reason)
        except Exception:
            detail = exc.reason
        return f"API error {exc.code}: {detail}"
    except Exception as exc:
        return f"Error: {exc}"


def _bm25_top_k(full_text: str, question: str, top_k: int, chunk_size: int = 400) -> list[str]:
    """Chunk text and return top-k chunks by BM25 score."""
    from rank_bm25 import BM25Okapi

    # Split into overlapping chunks of ~chunk_size chars at paragraph boundaries
    raw_paras = [p.strip() for p in full_text.split("\n\n") if len(p.strip()) > 60]
    if not raw_paras:
        return []

    # Merge very short paragraphs into chunks
    chunks: list[str] = []
    buf = ""
    for para in raw_paras:
        if len(buf) + len(para) < chunk_size:
            buf = (buf + " " + para).strip()
        else:
            if buf:
                chunks.append(buf)
            buf = para
    if buf:
        chunks.append(buf)

    if not chunks:
        return []

    tokenized = [c.lower().split() for c in chunks]
    bm25 = BM25Okapi(tokenized)
    scores = bm25.get_scores(question.lower().split())
    ranked = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
    return [chunks[i] for i in ranked[:top_k]]


def rag_query(question: str, paper_id: str | None = None, top_k: int = 5) -> str:
    try:
        with _db() as db:
            from sqlalchemy import desc, select

            from ..models.paper import PaperORM
            from ..models.summary import PaperSummaryORM

            if paper_id:
                orm = db.get(PaperORM, paper_id)
                if orm is None:
                    return f"Paper '{paper_id}' not found."
                p = orm.to_pydantic()
                lines = [
                    f"# RAG context — {p.title}",
                    f"ID: {paper_id}",
                    "",
                    "## Abstract",
                    p.abstract or "(none)",
                ]

                # Collect full text: prefer direct PDF extraction, fall back to OCR
                full_text: str | None = None
                text_source: str = ""

                if p.local_pdf_path:
                    pdf_path = Path(p.local_pdf_path)
                    if pdf_path.exists():
                        try:
                            import fitz

                            doc = fitz.open(str(pdf_path))
                            try:
                                extracted = "\n\n".join(page.get_text() for page in doc).strip()
                            finally:
                                doc.close()
                            if extracted:
                                full_text = extracted
                                text_source = "PDF text layer"
                        except Exception as e:
                            log.warning("rag_query: PDF extraction failed for %s: %s", paper_id, e)

                if not full_text:
                    try:
                        from ..processing.pdf_ocr import PDFOCRPipeline

                        s = _cfg()
                        # For papers with no PDF (e.g. Wikipedia), pass a dummy path;
                        # load_text checks ocr_dir first and falls back to pdf_path.parent.
                        pdf_path_for_lookup = (
                            Path(p.local_pdf_path) if p.local_pdf_path else Path(".")
                        )
                        ocr = PDFOCRPipeline.load_text(
                            pdf_path_for_lookup, paper_id, ocr_dir=s.ocr_dir
                        )
                        if ocr:
                            full_text = ocr
                            text_source = "cached text"
                    except Exception as e:
                        log.warning("rag_query: text load failed for %s: %s", paper_id, e)

                if full_text:
                    top_chunks = _bm25_top_k(full_text, question, top_k)
                    lines += ["", f"## Top {len(top_chunks)} passages by BM25 ({text_source})"]
                    for j, chunk in enumerate(top_chunks, 1):
                        lines.append(f"\n[{j}] {chunk[:700]}")
                elif p.local_pdf_path:
                    lines += [
                        "",
                        "(No text available: PDF has no text layer and no OCR has been run. "
                        "Call ocr_paper() to extract text via vision-LLM.)",
                    ]
                else:
                    lines += [
                        "",
                        "(No text cached for this article. "
                        "Re-ingest it to populate the text cache.)",
                    ]

                stmt = (
                    select(PaperSummaryORM)
                    .where(PaperSummaryORM.paper_id == paper_id)
                    .order_by(desc(PaperSummaryORM.generated_at))
                    .limit(1)
                )
                summ = db.execute(stmt).scalar_one_or_none()
                if summ:
                    s2 = summ.to_pydantic()
                    lines += [
                        "",
                        "## LLM Summary (TL;DR)",
                        s2.tldr,
                        "",
                        "Methodology:",
                        s2.methodology,
                    ]
                return "\n".join(lines)

            # Corpus-wide — search by abstract embeddings, RAG per-paper done on demand
            try:
                svc = _embed()
                results = svc._col.query(
                    query_texts=[question],
                    n_results=min(top_k, svc._col.count() or 1),
                    include=["distances"],
                )
                hits = list(zip(results["ids"][0], results["distances"][0]))
            except Exception as _sem_err:
                log.warning(
                    "rag_query: semantic search unavailable (%s), falling back to keyword search",
                    _sem_err,
                )
                from ..database.repository import PaperRepository

                papers = PaperRepository(db).search(query=question, limit=top_k)
                hits = [(p.id, None) for p in papers]

            lines = [
                f"# Corpus RAG context — '{question}'",
                f"[Note: using {'semantic' if hits and hits[0][1] is not None else 'keyword (semantic unavailable)'} search]",
                f"Retrieved {len(hits)} papers\n",
            ]
            for rank, (pid, dist) in enumerate(hits, 1):
                sim_str = f"  similarity={round(1.0 - float(dist), 3)}" if dist is not None else ""
                orm = db.get(PaperORM, pid)
                if orm is None:
                    continue
                p = orm.to_pydantic()
                lines += [
                    f"## [{rank}]{sim_str}",
                    f"ID: {pid}",
                    f"Title: {p.title}",
                    f"Date: {str(p.published_date or '')[:10]}  Venue: {p.venue or ''}",
                    "",
                    f"Abstract: {(p.abstract or '')[:500]}",
                ]
                stmt = (
                    select(PaperSummaryORM)
                    .where(PaperSummaryORM.paper_id == pid)
                    .order_by(desc(PaperSummaryORM.generated_at))
                    .limit(1)
                )
                summ = db.execute(stmt).scalar_one_or_none()
                if summ:
                    s2 = summ.to_pydantic()
                    lines += ["", f"TL;DR: {s2.tldr}", f"Methodology: {s2.methodology[:300]}"]
                lines.append("")
            return "\n".join(lines)
    except Exception as exc:
        log.exception("rag_query")
        return f"Error: {exc}"


_MERIDIAN_CSS_MARKER = "--rust:"  # present in every properly styled dashboard


def _inject_meridian_css(html: str) -> str:
    """If the HTML doesn't include Meridian tokens, inject the base CSS into <head>."""
    if _MERIDIAN_CSS_MARKER in html:
        return html  # already styled
    try:
        from ..processing.prompts import PROMPTS_DIR

        skill_path = PROMPTS_DIR / "skills" / "dashboard_style.md"
        if not skill_path.exists():
            return html
        raw = skill_path.read_text(encoding="utf-8")
        # Extract the first ```css … ``` block from the skill file
        import re as _re

        m = _re.search(r"```css\n(.*?)```", raw, _re.DOTALL)
        if not m:
            return html
        css = m.group(1).strip()
        style_tag = f"<style>\n{css}\n</style>"
        if "</head>" in html:
            return html.replace("</head>", f"{style_tag}\n</head>", 1)
        # No <head> — prepend
        return style_tag + "\n" + html
    except Exception:
        return html


def _workspace_dir(paper_id: str | None) -> Path:
    """Return the workspace subdirectory for a paper (or __global__ if none)."""
    base = _ROOT / "workspace"
    if paper_id:
        safe = paper_id.replace(":", "_").replace("/", "_").replace(" ", "_")
        d = base / safe
    else:
        d = base / "__global__"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_dashboard(filename: str, html: str, paper_id: str | None = None) -> str:
    """Write an HTML dashboard to the workspace folder, scoped to the current paper.

    Args:
        filename: Output filename (e.g. 'summary.html'). Must end in .html.
        html:     Full HTML content to write.
        paper_id: The active paper's ID (e.g. 'arxiv:2301.00001'). Always pass this
                  so the dashboard is linked to the paper and shown only for it.
    """
    safe_name = Path(filename).name
    if not safe_name:
        return "Error: invalid filename."
    d = _workspace_dir(paper_id)
    out = d / safe_name
    out.write_text(_inject_meridian_css(html), encoding="utf-8")
    sub = d.name
    return (
        f"Dashboard written to workspace/{sub}/{safe_name}.\n"
        f"Open in browser: http://localhost:8080/workspace/{sub}/{safe_name}"
    )


def list_workspace(paper_id: str | None = None) -> str:
    """List files in the workspace, optionally filtered to a specific paper.

    Args:
        paper_id: The active paper's ID. Pass this to see only dashboards for that paper.
    """
    if paper_id:
        d = _ROOT / "workspace" / paper_id.replace(":", "_").replace("/", "_").replace(" ", "_")
        if not d.exists() or not any(d.iterdir()):
            return f"No workspace files for paper '{paper_id}'."
        lines = [f"Workspace files for {paper_id}:\n"]
        sub = d.name
        for f in sorted(d.iterdir()):
            lines.append(
                f"  {f.name}  ({f.stat().st_size:,} bytes)"
                f"  → http://localhost:8080/workspace/{sub}/{f.name}"
            )
    else:
        base = _ROOT / "workspace"
        if not base.exists():
            return "Workspace is empty."
        all_files = [f for d in base.iterdir() if d.is_dir() for f in d.iterdir()]
        if not all_files:
            return "Workspace is empty."
        lines = ["All workspace files:\n"]
        for f in sorted(all_files):
            sub = f.parent.name
            lines.append(
                f"  {sub}/{f.name}  ({f.stat().st_size:,} bytes)"
                f"  → http://localhost:8080/workspace/{sub}/{f.name}"
            )
    return "\n".join(lines)


def extract_pdf_text(paper_id: str) -> str:
    """Extract and cache text from a paper's local PDF using PyMuPDF (no OCR, instant).

    Saves the full text to disk so it is available to rag_query() and shows up
    in the UI under 'Full OCR text'.  Does NOT return the full text — use
    rag_query(paper_id=..., question=...) to read specific passages without
    flooding the context window.

    Use ocr_paper() instead for scanned / image-only PDFs (no text layer).

    Args:
        paper_id: The paper's ID (e.g. 'arxiv:2304.00001').
    """
    try:
        import fitz  # PyMuPDF

        with _db() as db:
            from ..models.paper import PaperORM

            orm = db.get(PaperORM, paper_id)
            if orm is None:
                return f"Paper '{paper_id}' not found in database."
            if not orm.local_pdf_path:
                return f"No local PDF for '{paper_id}'. Download it first via the reader panel."
            pdf_path = Path(orm.local_pdf_path)
            if not pdf_path.exists():
                return f"PDF file not found at {pdf_path}."

        doc = fitz.open(str(pdf_path))
        try:
            page_count = len(doc)
            full = "\n\n".join(page.get_text() for page in doc).strip()
        finally:
            doc.close()

        if not full:
            return (
                "No extractable text layer found in this PDF. "
                "The PDF is likely scanned or image-based. "
                "Use ocr_paper() to run vision-LLM OCR on it."
            )

        # Persist text and set CACHE_FULLTEXT so summarize_paper() can proceed.
        try:
            from sqlalchemy import text as _sql

            from ..models.paper import CACHE_FULLTEXT
            from ..processing.pdf_ocr import PDFOCRPipeline

            s = _cfg()
            txt_path = PDFOCRPipeline.text_path_for(pdf_path, paper_id, ocr_dir=s.ocr_dir)
            if not txt_path.exists():
                txt_path.parent.mkdir(parents=True, exist_ok=True)
                txt_path.write_text(full, encoding="utf-8")
            # Direct SQL UPDATE — more reliable than ORM dirty-tracking across connections
            with _db() as db:
                db.execute(
                    _sql(
                        "UPDATE papers SET cache_flags = (COALESCE(cache_flags, 0) | :flag) WHERE id = :id"
                    ),
                    {"flag": CACHE_FULLTEXT, "id": paper_id},
                )
        except Exception as persist_err:
            log.error("extract_pdf_text: could not persist text for %s: %s", paper_id, persist_err)

        return (
            f"Text extracted and cached for '{paper_id}' ({page_count} pages, "
            f"{len(full):,} chars). "
            f"Use rag_query(paper_id='{paper_id}', question=...) to retrieve relevant passages."
        )
    except Exception as exc:
        log.exception("extract_pdf_text failed for %s", paper_id)
        return f"Error extracting PDF text for '{paper_id}': {exc}"


# ═══════════════════════════════════════════════════════════════════════════════
# execute_python tool
# ═══════════════════════════════════════════════════════════════════════════════


def execute_python(code: str, timeout: int = 30) -> str:
    """Execute Python code for calculations, data analysis, statistics, or plotting.

    Stdout/stderr is returned as a string.
    A variable WORKSPACE (pathlib.Path) is always available pointing to the
    writable workspace directory — use it for any file I/O, e.g.:
        with open(WORKSPACE / 'output.html', 'w') as f: ...
    Matplotlib figures are automatically saved there when plt.show() is called.

    Safe to use: numpy, pandas, matplotlib, scipy, math, json, datetime,
                 collections, pathlib, re, csv, io.
    Do NOT use: os.system, subprocess, open() with absolute paths outside WORKSPACE,
                __import__ tricks.

    Args:
        code:    Python source code to run.
        timeout: Max seconds to wait (default 30).
    """
    import os as _os
    import subprocess
    import sys
    import tempfile

    workspace = _workspace_dir(_get_active_paper())
    sub = workspace.name  # e.g. "arxiv_2301_00001" or "__global__"

    preamble = f"""
import pathlib as _pathlib
WORKSPACE = _pathlib.Path(r'{workspace}')
PAPER_ID = {_get_active_paper()!r}
WORKSPACE.mkdir(parents=True, exist_ok=True)
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as _plt
    _fig_n = [0]
    def _save_show(*a, **kw):
        _fig_n[0] += 1
        _p = WORKSPACE / f'plot_{{_fig_n[0]}}.png'
        _plt.savefig(_p, dpi=150, bbox_inches='tight')
        _plt.close()
        print(f'Plot saved: http://localhost:8080/workspace/{sub}/plot_{{_fig_n[0]}}.png')
    _plt.show = _save_show
except ImportError:
    pass
"""
    full_code = preamble + "\n" + code

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(full_code)
        tmp = f.name
    try:
        proc = subprocess.run(
            [sys.executable, tmp],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        if err:
            return f"{out}\n[stderr]:\n{err}" if out else f"[stderr]:\n{err}"
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Timeout: code ran longer than {timeout}s."
    except Exception as exc:
        return f"Error running code: {exc}"
    finally:
        _os.unlink(tmp)


def ingest_wikipedia_article(title: str, lang: str = "en") -> str:
    """Fetch a Wikipedia article by title and add it to the corpus.

    The article's full text is cached immediately so rag_query() works right away.
    Supports any Wikipedia language edition.

    Args:
        title: Wikipedia article title, e.g. 'Transformer (deep learning)'.
               Also accepts a Wikipedia URL like 'https://en.wikipedia.org/wiki/BERT_(language_model)'.
        lang:  Language edition ISO code (default 'en'). E.g. 'de', 'fr', 'ja'.
    """
    import re as _re

    # Extract title from URL if given
    url_match = _re.search(r"wikipedia\.org/wiki/([^?#]+)", title)
    if url_match:
        raw = urllib.parse.unquote(url_match.group(1)).replace("_", " ")
        # also pick up the lang from the URL subdomain
        lang_match = _re.search(r"//([a-z]{2,3})\.wikipedia\.org", title)
        if lang_match:
            lang = lang_match.group(1)
        title = raw

    try:
        from sqlalchemy import text as _sql

        from ..config.settings import RateLimitSettings
        from ..database.repository import PaperRepository
        from ..ingestion.wikipedia_fetcher import WikipediaFetcher

        s = _cfg()
        fetcher = WikipediaFetcher(RateLimitSettings(), ocr_dir=s.ocr_dir)
        paper = fetcher.fetch_by_title(lang, title)
        fetcher.close()

        if paper is None:
            return f"Wikipedia article not found: '{title}' (lang={lang})."

        with _db() as db:
            PaperRepository(db).upsert_many([paper])
            # Ensure CACHE_FULLTEXT is persisted (upsert_many may not update flags for existing rows)
            db.execute(
                _sql(
                    "UPDATE papers SET cache_flags = (COALESCE(cache_flags, 0) | :flag) WHERE id = :id"
                ),
                {"flag": 32, "id": paper.id},  # 32 = CACHE_FULLTEXT
            )

        # Trigger embedding so the article appears in semantic search
        enc = urllib.parse.quote(paper.id, safe="")
        try:
            _http(f"/papers/{enc}/embed", method="POST")
        except Exception as e:
            log.warning("ingest_wikipedia_article: embed trigger failed (non-fatal): %s", e)

        return (
            f"Wikipedia article ingested: '{paper.title}'\n"
            f"ID: {paper.id}\n"
            f"Text cached: yes ({len(paper.abstract or '')} chars abstract).\n"
            f"Use rag_query(paper_id='{paper.id}', question=...) to search its content."
        )
    except Exception as exc:
        log.exception("ingest_wikipedia_article")
        return f"Error: {exc}"


def graph_traverse(
    start_paper_id: str,
    edge_types: str = "all",
    include_citations: bool = False,
    include_semantic: bool = True,
    semantic_threshold: float = 0.65,
    semantic_k: int = 5,
    max_depth: int = 3,
    max_nodes: int = 25,
) -> str:
    """Walk the knowledge graph outward from a starting paper through typed
    relationships and/or semantic similarity edges.

    Use this when a query involves impact analysis, dependency chains, or
    conceptual lineage — for example "what does X influence?", "what builds
    on Y?", "what are the downstream effects of Z?". Graph traversal catches
    connections that keyword search misses because it follows the structure of
    the knowledge graph rather than matching text.

    The traversal result is also stored server-side so the Graph tab in the UI
    can visualise the trail immediately (nodes are highlighted by depth).

    Args:
        start_paper_id:     Paper ID to start from, e.g. 'arxiv:2301.00001'.
        edge_types:         Comma-separated list of LLM-typed edge types to
                            follow, or "all" to follow every typed edge.
                            Valid types: extends, supersedes, challenges,
                            applies, uses, surveys, baseline, concurrent,
                            semantic (similarity edges), cites, cited_by.
                            Example: "uses,depends_on,extends"
        include_citations:  Also walk citation edges (A cites B / B cites A).
        include_semantic:   Also walk high-similarity semantic neighbour edges.
        semantic_threshold: Minimum cosine similarity to follow a semantic edge
                            (0.0–1.0, default 0.65).
        semantic_k:         Max semantic neighbours to consider per node.
        max_depth:          Maximum hops from the start node (default 3).
        max_nodes:          Stop after visiting this many nodes (default 25).

    Returns a structured text showing every node visited, the edge that led
    there, and the depth level — formatted as a traversal trail.
    """
    try:
        types_list = (
            None
            if edge_types.strip().lower() == "all"
            else [t.strip() for t in edge_types.split(",")]
        )
        result = _http(
            "/papers/traverse",
            method="POST",
            body={
                "start_id": start_paper_id,
                "edge_types": types_list,
                "include_citations": include_citations,
                "include_semantic": include_semantic,
                "semantic_k": semantic_k,
                "semantic_threshold": semantic_threshold,
                "max_depth": max_depth,
                "max_nodes": max_nodes,
            },
        )
        nodes = result.get("nodes_visited", [])
        if not nodes:
            # Give the agent a specific reason so it doesn't retry blindly
            with _db() as db:
                from ..models.paper import PaperORM

                orm = db.get(PaperORM, start_paper_id)
            if orm is None:
                return (
                    f"Paper '{start_paper_id}' is not in the corpus. "
                    "Ingest it first with get_paper() or ingest_papers()."
                )
            cache_flags = orm.cache_flags or 0
            if not (cache_flags & 2):  # CACHE_EMBEDDINGS = 2
                return (
                    f"Paper '{start_paper_id}' is in the corpus but has not been embedded yet. "
                    "Call embed_paper() first, then retry graph_traverse()."
                )
            return (
                f"No graph edges found from '{start_paper_id}'. "
                "The paper is embedded but has no typed or semantic edges at the requested threshold. "
                "Try lowering semantic_threshold (currently {semantic_threshold}) or include_citations=True."
            )

        lines = [
            f"Graph traversal from: {result.get('start_title', start_paper_id)}",
            f"Stopped: {result.get('stopped_reason', '?')}  |  {result['total_nodes']} nodes, {result['total_edges']} edges\n",
            "TRAVERSAL TRAIL (depth → paper [via edge_type]):",
        ]
        by_depth: dict[int, list] = {}
        for n in nodes:
            by_depth.setdefault(n["depth"], []).append(n)

        for depth in sorted(by_depth):
            depth_nodes = by_depth[depth]
            lines.append(f"\n  Depth {depth}:")
            for n in depth_nodes:
                via = ""
                if n["via_edge_type"]:
                    dir_arrow = "→" if n.get("via_direction") == "out" else "←"
                    conf = (
                        f" ({n['via_confidence']:.2f})"
                        if n.get("via_confidence") is not None
                        else ""
                    )
                    via = (
                        f"  [{n['via_edge_type']}{conf}] {dir_arrow} from: {n.get('via_from', '?')}"
                    )
                    if n.get("via_description"):
                        via += f"\n              ↳ {n['via_description']}"
                lines.append(f"    • [{n['id']}] {n['title']}{via}")

        lines.append(
            "\nThe traversal trail is now visible in the Graph tab (nodes highlighted by depth)."
        )
        lines.append("Use rag_query(paper_id=..., question=...) to dig into any node's content.")
        return "\n".join(lines)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode()).get("detail", exc.reason)
        except Exception:
            detail = exc.reason
        return f"API error {exc.code}: {detail}"
    except Exception as exc:
        return f"Error: {exc}"


def get_entities(paper_id: str) -> str:
    """Return the structured entities and typed relationships extracted from a paper.

    Entities are named things (people, projects, libraries, concepts, datasets, methods,
    organizations, files, decisions) that the LLM identified as significant.
    Relationships are directed typed edges between them (e.g. "uses", "depends_on",
    "contradicts", "caused", "supersedes").

    Entity extraction runs automatically after summarization. Use
    extract_entities(paper_id) to trigger it manually if entities are missing.

    Args:
        paper_id: Paper ID, e.g. 'arxiv:2301.00001'.
    """
    try:
        enc = urllib.parse.quote(paper_id, safe="")
        data = _http(f"/papers/{enc}/entities")
        entities = data.get("entities", [])
        relationships = data.get("relationships", [])
        if not entities:
            return f"No entities extracted yet for '{paper_id}'. Run extract_entities('{paper_id}') first."
        lines = [f"Entities ({len(entities)}):"]
        for e in entities:
            attrs = e.get("attributes", {})
            attr_str = (", ".join(f"{k}={v}" for k, v in attrs.items()))[:80] if attrs else ""
            lines.append(f"  [{e['type']}] {e['name']}" + (f" — {attr_str}" if attr_str else ""))
        lines.append(f"\nRelationships ({len(relationships)}):")
        for r in relationships:
            conf = f" (conf={r['confidence']:.2f})" if r.get("confidence") else ""
            desc = f": {r['description']}" if r.get("description") else ""
            lines.append(f"  {r['from']} --[{r['type']}]--> {r['to']}{desc}{conf}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: {exc}"


def extract_entities(paper_id: str) -> str:
    """Trigger LLM entity extraction for a paper. Runs in the background.

    Extracts structured entities (people, projects, libraries, concepts, datasets,
    methods, organizations, files, decisions) and typed relationships between them.
    After this completes (~10–30 seconds), call get_entities(paper_id) to retrieve results.

    Args:
        paper_id: Paper ID, e.g. 'arxiv:2301.00001'.
    """
    try:
        enc = urllib.parse.quote(paper_id, safe="")
        _http(f"/papers/{enc}/entities/extract", method="POST")
        return f"Entity extraction queued for '{paper_id}'. Call get_entities('{paper_id}') after ~20 seconds."
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode()).get("detail", exc.reason)
        except Exception:
            detail = exc.reason
        return f"API error {exc.code}: {detail}"
    except Exception as exc:
        return f"Error: {exc}"


def add_note(paper_id: str, content: str) -> str:
    """Add a research note to a specific paper. The note will appear in the Notes tab
    in the reader and is attributed to the agent.

    Use this to record observations, connections to other work, questions, or
    follow-up ideas while analysing a paper.

    Args:
        paper_id: Paper ID, e.g. 'arxiv:2301.00001'.
        content:  The note text (supports Markdown).
    """
    try:
        enc = urllib.parse.quote(paper_id, safe="")
        result = _http(
            f"/users/me/papers/{enc}/notes",
            method="POST",
            body={"content": content, "source": "agent"},
        )
        return f"Note added to '{paper_id}' (id={result.get('id')})."
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode()).get("detail", exc.reason)
        except Exception:
            detail = exc.reason
        return f"API error {exc.code}: {detail}"
    except Exception as exc:
        return f"Error: {exc}"


def ingest_webpage(url: str, paper_id: str | None = None) -> str:
    """Fetch a web page via screenshot OCR and add it to the corpus.

    Uses a headless browser to scroll through the page viewport by viewport,
    screenshots each position, and runs vision OCR — no DOM/HTML parsing.
    This works reliably for JS-heavy pages, SPAs, and image-heavy layouts.

    The page is assigned a synthetic ID of the form ``web:<hash>`` and can
    be queried immediately with rag_query().

    Args:
        url:      Full URL to ingest, e.g. 'https://example.com/blog/post'.
        paper_id: Optional — the corpus paper currently being discussed. When
                  supplied, the web page is linked to that paper in the UI.
    """
    try:
        body: dict = {"url": url, "force_ocr": True}
        if paper_id:
            body["paper_id"] = paper_id
        result = _http("/web/ingest", method="POST", body=body)
        pid = result.get("paper_id", "?")
        title = result.get("title", url)
        chars = result.get("char_count", 0)
        shots = result.get("screenshot_count", 0)

        # ── Quality assessment ────────────────────────────────────────────────
        if chars < 200:
            quality = "POOR"
            quality_note = (
                f"Only {chars} characters extracted from {shots} screenshot(s). "
                "The page likely requires authentication, is paywalled, or is "
                "almost entirely image-based with no readable text. "
                "ACTION REQUIRED: inform the user that this page could not be read. "
                "Do not call rag_query — it will return nothing useful."
            )
        elif chars < 800:
            quality = "MARGINAL"
            quality_note = (
                f"{chars} characters from {shots} screenshot(s). Content may be partial. "
                "Proceed with rag_query but note the limited content to the user."
            )
        else:
            quality = "GOOD"
            quality_note = f"{chars} characters from {shots} screenshot(s)."

        return (
            f"Web page ingested (OCR): '{title}'\n"
            f"ID: {pid}\n"
            f"URL: {url}\n"
            f"Characters extracted: {chars} · Screenshots: {shots}\n"
            f"Quality: {quality} — {quality_note}\n"
            + (
                f"Use rag_query(paper_id='{pid}', question=...) to search its content."
                if quality != "POOR"
                else ""
            )
        )
    except Exception as exc:
        log.exception("ingest_webpage")
        return f"Error ingesting webpage: {exc}"


def update_meridian_context(content: str) -> str:
    """Rewrite the agent's living context file (MERIDIAN.md) with new content.

    Use this proactively to record user preferences, research focus, patterns,
    and accumulated observations so they persist across sessions.
    The file is injected into your system prompt on every request.

    Args:
        content: Full new content for MERIDIAN.md (plain markdown, no frontmatter needed).
                 This *replaces* the current file — include everything you want to keep.
    """
    try:
        from ..processing.prompts import PROMPTS_DIR

        path = PROMPTS_DIR / "MERIDIAN.md"
        frontmatter = (
            "---\n"
            "name: MERIDIAN\n"
            "description: >\n"
            "  Living context file for the Meridian agent. Injected into the system prompt on\n"
            "  every request. Update this file using update_meridian_context() to record user\n"
            "  preferences, research focus, tool patterns, and accumulated knowledge.\n"
            "  Edits take effect on the next message — no restart needed.\n"
            "---\n\n"
        )
        path.write_text(frontmatter + content.strip() + "\n", encoding="utf-8")
        return "MERIDIAN.md updated. Changes will take effect on your next message."
    except Exception as exc:
        log.exception("update_meridian_context")
        return f"Error updating MERIDIAN.md: {exc}"


def ingest_papers(
    interests: list[str],
    sources: list[str] | None = None,
    days: int = 30,
) -> str:
    """Trigger a batch ingest from one or more paper sources.

    Fetches papers matching *interests* from the specified *sources* over the last
    *days* days, then stores and enriches them in the local corpus.

    IMPORTANT — use sparingly:
    - Call at most ONCE per turn with at most 3 focused keywords.
    - First try search_papers / semantic_search; only ingest if nothing relevant is found.
    - Use days=7 to 14 unless the user explicitly asks for a longer window.
    - Never ingest speculatively or to "be thorough".

    Args:
        interests: Narrow, specific keywords (e.g. ["YOLOv9 object detection"]).
                   At most 3 terms. Broad topics like "machine learning" will pull
                   hundreds of irrelevant papers.
        sources: Source names. Valid values: "arXiv", "bioRxiv", "PubMed", "OpenReview".
                 Defaults to ["arXiv", "bioRxiv", "PubMed"].
        days: How many calendar days back to search. Default 30; prefer 7–14.

    Returns:
        A summary string with paper counts, errors, and duration.
    """
    if sources is None:
        sources = ["arXiv", "bioRxiv", "PubMed"]

    body = {
        "interests": interests,
        "sources": sources,
        "window_days": days,
    }
    try:
        resp = _http("/ingest/run", method="POST", body=body)
        job_id = resp.get("job_id")
        if not job_id:
            detail = resp.get("detail") or resp.get("error") or str(resp)
            return f"Ingest request failed — server did not return a job_id. Detail: {detail}"

        # Poll job status until done (the pipeline runs in a background thread)
        import time as _time

        for _ in range(180):  # wait up to 3 minutes
            _time.sleep(2)
            try:
                active = _http("/ingest/active")
                jobs = active.get("jobs", [])
                if not any(j["id"] == job_id for j in jobs):
                    break
            except Exception:
                pass

        summary = _http("/ingest/summary")
        total = summary.get("total_papers", "?")
        return (
            f"Ingest complete. Sources: {', '.join(sources)}. "
            f"Interests: {', '.join(interests)}. "
            f"Total papers in corpus: {total}."
        )
    except Exception as exc:
        log.exception("ingest_papers")
        return f"Ingest failed: {exc}"


# ═══════════════════════════════════════════════════════════════════════════════
# OpenAI Agents SDK tool objects  (auto-schema from type hints + docstrings)
# ═══════════════════════════════════════════════════════════════════════════════

from agents import function_tool

AGENT_TOOLS = [
    function_tool(search_papers),
    function_tool(semantic_search),
    function_tool(find_similar_papers),
    function_tool(get_paper),
    function_tool(list_papers),
    function_tool(query_database),
    function_tool(summarize_paper),
    function_tool(embed_paper),
    function_tool(ocr_paper),
    function_tool(rag_query),
    function_tool(create_dashboard),
    function_tool(list_workspace),
    function_tool(extract_pdf_text),
    function_tool(execute_python),
    function_tool(add_note),
    function_tool(ingest_papers),
    function_tool(ingest_wikipedia_article),
    function_tool(ingest_webpage),
    function_tool(get_entities),
    function_tool(extract_entities),
    function_tool(graph_traverse),
    function_tool(update_meridian_context),
]

# ── Legacy: keep TOOL_DEFINITIONS name for any other callers ────────────────
# (only the MCP server uses these; the agent loop uses AGENT_TOOLS now)
TOOL_DEFINITIONS = [
    {
        "name": "search_papers",
        "description": "Keyword search across paper titles and abstracts in the local SQLite database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text search string."},
                "limit": {"type": "integer", "description": "Max results (default 10)."},
                "since_days": {"type": "integer", "description": "Only papers from last N days."},
                "source": {
                    "type": "string",
                    "description": "Filter: 'arxiv'|'biorxiv'|'openreview'|'pubmed'.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "semantic_search",
        "description": (
            "Find papers semantically similar to a natural-language query using vector embeddings. "
            "Requires papers to have been embedded first (Graph tab → Embed corpus)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Research topic or concept."},
                "limit": {"type": "integer", "description": "Number of results (default 8)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "find_similar_papers",
        "description": (
            "Find papers similar to a given paper using its stored embedding "
            "(same as the Graph tab neighbour edges)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "Paper ID, e.g. 'arxiv:2304.00001'."},
                "k": {"type": "integer", "description": "Number of neighbours (default 10)."},
            },
            "required": ["paper_id"],
        },
    },
    {
        "name": "get_paper",
        "description": "Retrieve full metadata, abstract, and LLM-generated summary for a paper.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "Paper ID."},
                "include_summary": {
                    "type": "boolean",
                    "description": "Include LLM summary (default true).",
                },
            },
            "required": ["paper_id"],
        },
    },
    {
        "name": "list_papers",
        "description": "List papers from the local database, newest first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results (default 20, max 2000)."},
                "source": {"type": "string", "description": "Filter by source."},
                "since_days": {"type": "integer", "description": "Only last N days."},
            },
        },
    },
    {
        "name": "query_database",
        "description": (
            "Run a read-only SQL SELECT query against the SQLite database. "
            "Tables: papers, paper_summaries, paper_edges, pins, collections, "
            "collection_items, chats, chat_messages, ingest_history."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A SQL SELECT or WITH statement."},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "summarize_paper",
        "description": (
            "Trigger LLM summarization for a paper (async — runs in background). "
            "Call get_paper() after 30–120 s to read the result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "Paper ID."},
            },
            "required": ["paper_id"],
        },
    },
    {
        "name": "ocr_paper",
        "description": (
            "Trigger vision-LLM OCR text extraction for a paper's local PDF (async). "
            "Afterwards rag_query(paper_id=...) will use the full text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_id": {"type": "string", "description": "Paper ID. PDF must be downloaded."},
            },
            "required": ["paper_id"],
        },
    },
    {
        "name": "rag_query",
        "description": (
            "Retrieve relevant context to answer a research question. "
            "With paper_id: RAG within that paper (uses OCR text if available). "
            "Without paper_id: corpus-wide RAG via semantic search. "
            "Returns SOURCE MATERIAL only — synthesise the answer yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Research question or topic."},
                "paper_id": {
                    "type": "string",
                    "description": "Scope to a single paper (optional).",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Context chunks to retrieve (default 5).",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "create_dashboard",
        "description": (
            "Write an HTML dashboard to the workspace folder. "
            "Immediately accessible at http://localhost:8080/workspace/<filename>. "
            "Use for interactive D3 visualisations, analysis reports, paper comparisons."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "e.g. 'analysis.html'. No path traversal.",
                },
                "html": {"type": "string", "description": "Full HTML content."},
            },
            "required": ["filename", "html"],
        },
    },
    {
        "name": "list_workspace",
        "description": "List all files in the agent workspace folder with their browser URLs.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ingest_webpage",
        "description": (
            "Fetch a web page via screenshot OCR and add it to the corpus. "
            "Scrolls through the page viewport by viewport, screenshots and OCRs each one. "
            "The page gets a synthetic ID (web:<hash>) and can be queried with rag_query() immediately."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to ingest, e.g. 'https://example.com/post'.",
                },
                "paper_id": {
                    "type": "string",
                    "description": "Optional corpus paper to associate this page with.",
                },
            },
            "required": ["url"],
        },
    },
]
