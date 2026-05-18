"""IngestionPipeline: orchestrates all fetchers, deduplication, enrichment, and storage.

Execution order:
  1. Fetch from all requested sources (arXiv, bioRxiv, OpenReview).
  2. Deduplicate by canonical paper ID.
  3. Enrich with Semantic Scholar (citation counts, TLDR, PDF URLs).
  4. Persist papers + citation edges to SQLite.
  5. Optionally download PDFs.

Design choices:
- Fetchers run sequentially (not concurrently) because each already respects
  its own rate limit.  True parallelism is possible later with thread pools
  but adds complexity for diminishing returns at our scale.
- Deduplication by canonical ID means the same paper appearing on arXiv and
  OpenReview (same conference preprint) is stored once with the arXiv ID
  taking precedence (it's assigned first by our ID factory helpers).
- ``dry_run`` mode fetches and enriches but skips all database and disk writes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from ..config.settings import Settings
from ..database.engine import build_engine, build_session_factory, get_session
from ..database.repository import CitationRepository, PaperRepository
from ..models.paper import Paper
from ..models.taxonomy import classify as classify_topics
from .arxiv_fetcher import ArxivFetcher
from .base import BaseFetcher, FetchQuery
from .biorxiv_fetcher import BiorxivFetcher
from .openalex_enricher import OpenAlexClient
from .openreview_fetcher import OpenReviewFetcher
from .pdf_downloader import PDFDownloader
from .pubmed_fetcher import PubMedFetcher
from .semantic_scholar import SemanticScholarClient
from .wikipedia_fetcher import WikipediaFetcher

logger = logging.getLogger(__name__)
console = Console()


@dataclass
class PipelineResult:
    """Statistics from a completed ingestion run."""

    total_fetched: int = 0
    total_unique: int = 0
    total_new: int = 0
    total_enriched: int = 0
    total_citations: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def summary(self) -> str:
        return (
            f"{self.total_new} new papers stored "
            f"(fetched {self.total_fetched}, unique {self.total_unique}), "
            f"{self.total_citations} citation edges, "
            f"{len(self.errors)} errors, "
            f"{self.duration_seconds:.1f}s"
        )


class IngestionPipeline:
    """Single entry point for a complete paper ingestion cycle."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        settings.ensure_directories()

        engine = build_engine(settings.database.sqlite_path)
        self._session_factory = build_session_factory(engine)

        limits = settings.rate_limits
        self._fetchers: list[BaseFetcher] = [
            ArxivFetcher(limits),
            BiorxivFetcher(limits),
            OpenReviewFetcher(
                limits,
                username=settings.openreview_username,
                password=settings.openreview_password,
            ),
            PubMedFetcher(limits, api_key=settings.pubmed_api_key),
            WikipediaFetcher(limits, ocr_dir=settings.ocr_dir),
        ]
        self._s2 = SemanticScholarClient(settings.semantic_scholar_api_key, limits)
        self._openalex = OpenAlexClient(delay_seconds=0.12)
        self._pdf_dl = PDFDownloader(settings.papers_dir)

    def run(
        self,
        days_lookback: int | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        sources: list[str] | None = None,
        interests: list[str] | None = None,
        arxiv_categories: list[str] | None = None,
        biorxiv_categories: list[str] | None = None,
        wikipedia_languages: list[str] | None = None,
        download_pdfs: bool = False,
        enrich: bool = True,
        dry_run: bool = False,
        progress_callback=None,
    ) -> PipelineResult:
        """Execute a full ingestion cycle and return a result summary."""
        started = datetime.now(tz=timezone.utc)
        result = PipelineResult()
        prefs = self._settings.preferences

        now = datetime.now(tz=timezone.utc)
        # Explicit date range takes priority over days_lookback
        if date_from and date_to:
            start_date = date_from
            end_date = date_to
        else:
            days = days_lookback or prefs.days_lookback
            start_date = now - timedelta(days=days)
            end_date = now

        # Interests / categories from the UI override the static settings.
        keywords = interests if interests else prefs.keywords
        query = FetchQuery(
            keywords=keywords,
            categories=arxiv_categories if arxiv_categories is not None else prefs.arxiv_categories,
            biorxiv_categories=biorxiv_categories
            if biorxiv_categories is not None
            else prefs.biorxiv_categories,
            wikipedia_languages=wikipedia_languages
            if wikipedia_languages is not None
            else prefs.wikipedia_languages,
            venues=prefs.venues,
            start_date=start_date,
            end_date=end_date,
            max_results=prefs.max_results_per_query,
        )

        def _emit(frame: dict) -> None:
            if progress_callback:
                progress_callback(frame)

        active = [f for f in self._fetchers if sources is None or f.source_name in sources]

        # ── Step 1: Fetch ─────────────────────────────────────────────────
        all_papers: list[Paper] = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Fetching…", total=len(active))
            for fetcher in active:
                progress.update(task, description=f"[cyan]Fetching {fetcher.source_name}…")
                fetch_result = fetcher.fetch(query)
                all_papers.extend(fetch_result.papers)
                result.errors.extend(fetch_result.errors)
                result.total_fetched += fetch_result.count
                progress.advance(task)
                _emit(
                    {
                        "type": "phase",
                        "name": "fetch",
                        "note": f"{fetcher.source_name}: {fetch_result.count} papers",
                    }
                )

        # ── Step 2: Deduplicate + classify topics ─────────────────────────
        seen: set[str] = set()
        unique: list[Paper] = []
        for paper in all_papers:
            if paper.id not in seen:
                seen.add(paper.id)
                if not paper.topics:
                    paper.topics = classify_topics(paper.title, paper.abstract)
                unique.append(paper)
        result.total_unique = len(unique)
        console.print(
            f"[green]Fetched [bold]{result.total_fetched}[/bold] papers → [bold]{result.total_unique}[/bold] unique"
        )
        _emit(
            {
                "type": "phase",
                "name": "dedup",
                "note": f"{result.total_unique} unique from {result.total_fetched} fetched",
            }
        )

        # ── Step 3: Enrichment (S2 + OpenAlex) ───────────────────────────
        if enrich:
            s2_enabled = bool(self._settings.semantic_scholar_api_key)
            if s2_enabled:
                console.print("[yellow]Enriching via Semantic Scholar…")
            console.print("[yellow]Enriching via OpenAlex…")
            _emit(
                {
                    "type": "phase",
                    "name": "enrich",
                    "note": f"OpenAlex enrichment for {len(unique)} papers",
                }
            )
            enriched: list[Paper] = []
            for paper in unique:
                # Wikipedia articles have no DOI/arXiv ID; skip external enrichment
                from ..models.paper import PaperSource as _PS

                if paper.source == _PS.WIKIPEDIA:
                    enriched.append(paper)
                    result.total_enriched += 1
                    continue
                if s2_enabled:
                    paper = self._s2.enrich_paper(paper)
                # OpenAlex fills whatever S2 left blank (or everything if S2 off)
                paper = self._openalex.enrich_paper(paper)
                if paper.citation_count:
                    logger.debug(
                        "OpenAlex enriched %s: cited_by=%d ref_count=%s vel=%s",
                        paper.id,
                        paper.citation_count,
                        paper.reference_count,
                        paper.velocity_12w[:3] if paper.velocity_12w else [],
                    )
                enriched.append(paper)
                result.total_enriched += 1
            unique = enriched

        # ── Step 4: Persist ───────────────────────────────────────────────
        if not dry_run:
            with get_session(self._session_factory) as session:
                paper_repo = PaperRepository(session)
                citation_repo = CitationRepository(session)
                result.total_new = paper_repo.upsert_many(unique)

                if enrich:
                    for paper in unique:
                        # S2 references (richer, has influence flags)
                        if paper.semantic_scholar_id:
                            refs = self._s2.get_references(paper)
                            result.total_citations += citation_repo.upsert_many(refs)
                        elif paper.arxiv_id or paper.doi:
                            # Fall back to OpenAlex reference list
                            refs = self._openalex.get_references(paper)
                            result.total_citations += citation_repo.upsert_many(refs)
        else:
            console.print("[dim]Dry run — skipping database write")

        # ── Step 5: PDF download ──────────────────────────────────────────
        if download_pdfs and not dry_run:
            console.print("[yellow]Downloading PDFs…")
            _emit({"type": "phase", "name": "pdf", "note": f"Downloading up to {len(unique)} PDFs"})
            for paper in unique:
                if paper.pdf_url:
                    local_path = self._pdf_dl.download(paper.pdf_url, paper.id)
                    if local_path:
                        with get_session(self._session_factory) as session:
                            PaperRepository(session).mark_downloaded(paper.id, str(local_path))

        result.duration_seconds = (datetime.now(tz=timezone.utc) - started).total_seconds()
        console.print(f"[bold green]Done — {result.summary()}")
        return result
