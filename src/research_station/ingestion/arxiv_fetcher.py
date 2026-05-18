"""arXiv fetcher using the official ``arxiv`` Python client.

Design choices:
- The ``arxiv`` library manages its own rate limiting (3 s default between
  pages); we pass our configured delay to its Client constructor.
- Version numbers are stripped from arXiv IDs (``2301.00001v2`` → ``2301.00001``)
  so that updates to the same paper don't create duplicate DB rows.
- Date filtering is applied client-side because arXiv's API sorts by
  submission date but doesn't support strict date-range filtering in the
  query string for category searches.
"""

from __future__ import annotations

import logging
from datetime import timezone

import arxiv

from ..config.settings import RateLimitSettings
from ..models.paper import Author, Paper, PaperSource
from .base import BaseFetcher, FetchQuery, FetchResult

logger = logging.getLogger(__name__)


class ArxivFetcher(BaseFetcher):
    """Fetches papers from arXiv via the public API."""

    source_name = "arxiv"

    def __init__(self, rate_limits: RateLimitSettings) -> None:
        super().__init__(rate_limits)
        self._client = arxiv.Client(
            page_size=100,
            delay_seconds=rate_limits.arxiv_delay_seconds,
            num_retries=rate_limits.max_retries,
        )

    def fetch(self, query: FetchQuery) -> FetchResult:
        """Fetch arXiv papers matching *query*.

        Builds a query string from categories and keywords, injects a
        ``submittedDate`` range directly into the arXiv query when a date
        window is specified so the API filters server-side (avoids fetching
        thousands of recent papers just to discard them). A client-side pass
        is still applied as a safety net for timezone edge cases.
        """
        result = FetchResult(source=self.source_name)
        query_str = self._build_query_string(query)

        logger.info("arXiv search query: %r (max=%d)", query_str, query.max_results)

        try:
            search = arxiv.Search(
                query=query_str,
                max_results=query.max_results,
                sort_by=arxiv.SortCriterion.SubmittedDate,
                sort_order=arxiv.SortOrder.Descending,
            )
            for entry in self._client.results(search):
                paper = self._entry_to_paper(entry)
                # When results are newest-first and we drop below start_date,
                # all remaining papers will also be too old — stop early.
                if query.start_date and paper.published_date:
                    pub = paper.published_date
                    if pub.tzinfo is None:
                        pub = pub.replace(tzinfo=timezone.utc)
                    start = query.start_date
                    if start.tzinfo is None:
                        start = start.replace(tzinfo=timezone.utc)
                    if pub < start:
                        break
                if _passes_date_filter(paper, query):
                    result.papers.append(paper)
        except Exception as exc:
            logger.error("arXiv fetch failed: %s", exc)
            result.errors.append(str(exc))

        logger.info("arXiv: collected %d papers", result.count)
        return result

    # ── Private ───────────────────────────────────────────────────────────

    @staticmethod
    def _build_query_string(query: FetchQuery) -> str:
        """Compose an arXiv query string from categories, keywords, and dates.

        When interests/keywords are provided they drive the search directly
        (no category restriction) so niche topics aren't filtered out.
        Each multi-word interest is truncated to its first two words before
        quoting — exact phrase matching on long interests yields zero results.

        When no keywords are given the query falls back to the ML category list.

        A ``submittedDate`` clause is appended whenever start_date or end_date
        is set, pushing date filtering to the arXiv API rather than client-side.
        The arXiv format is ``[YYYYMMDD0000 TO YYYYMMDD2359]``.
        """
        if query.keywords:
            phrases = []
            for kw in query.keywords:
                phrase = kw.strip()
                phrases.append(f'(ti:"{phrase}" OR abs:"{phrase}")')
            base = " OR ".join(phrases)
        elif query.categories:
            cat_clause = " OR ".join(f"cat:{c}" for c in query.categories)
            base = f"({cat_clause})"
        else:
            base = "cat:cs.LG"

        # Inject server-side date filter when a window is explicitly set
        if query.start_date or query.end_date:
            start_str = (
                query.start_date.strftime("%Y%m%d0000") if query.start_date else "000000000000"
            )
            end_str = query.end_date.strftime("%Y%m%d2359") if query.end_date else "999999999999"
            return f"({base}) AND submittedDate:[{start_str} TO {end_str}]"

        return base

    @staticmethod
    def _entry_to_paper(entry: arxiv.Result) -> Paper:
        """Convert an ``arxiv.Result`` to a ``Paper`` model."""
        # Strip the version suffix from short IDs (e.g. "2301.00001v2")
        arxiv_id = entry.get_short_id().split("v")[0]

        authors = [
            Author(name=a.name, affiliations=list(getattr(a, "affiliations", [])))
            for a in entry.authors
        ]

        pub = entry.published
        upd = entry.updated
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        if upd.tzinfo is None:
            upd = upd.replace(tzinfo=timezone.utc)

        return Paper(
            id=Paper.make_arxiv_id(arxiv_id),
            title=entry.title.strip(),
            abstract=entry.summary.strip() if entry.summary else None,
            authors=authors,
            categories=list(entry.categories),
            source=PaperSource.ARXIV,
            published_date=pub,
            updated_date=upd,
            pdf_url=entry.pdf_url,
            doi=entry.doi,
            arxiv_id=arxiv_id,
            raw_metadata={
                "comment": entry.comment,
                "journal_ref": entry.journal_ref,
                "primary_category": (
                    getattr(entry.primary_category, "id", str(entry.primary_category))
                    if entry.primary_category
                    else None
                ),
            },
        )


def _passes_date_filter(paper: Paper, query: FetchQuery) -> bool:
    """Return True if the paper falls within the query's date window."""
    pub = paper.published_date
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    if query.start_date:
        start = query.start_date
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if pub < start:
            return False
    if query.end_date:
        end = query.end_date
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if pub > end:
            return False
    return True
