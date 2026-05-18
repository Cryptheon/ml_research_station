"""bioRxiv / medRxiv fetcher using their public REST API.

API reference: https://api.biorxiv.org/
Endpoints used:
  GET /details/{server}/{start_date}/{end_date}/{cursor}/json
  Returns up to 100 records per page; paginated via cursor offset.

Design choices:
- Both bioRxiv and medRxiv share the same API structure, so this fetcher
  accepts a ``server`` parameter to handle both.
- Category filtering is post-hoc (the API returns all categories for a date
  range); keyword filtering is applied against title + abstract.
- Author strings in the bioRxiv API are semicolon-separated; we split and
  strip whitespace to produce Author objects.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from ..config.settings import RateLimitSettings
from ..models.paper import Author, Paper, PaperSource
from .base import BaseFetcher, FetchQuery, FetchResult

logger = logging.getLogger(__name__)

_API_BASE = "https://api.biorxiv.org/details"


class BiorxivFetcher(BaseFetcher):
    """Fetches preprints from bioRxiv or medRxiv."""

    source_name = "biorxiv"

    def __init__(
        self,
        rate_limits: RateLimitSettings,
        server: str = "biorxiv",
    ) -> None:
        super().__init__(rate_limits)
        # "biorxiv" or "medrxiv"
        self._server = server
        self._client = httpx.Client(timeout=20.0)

    def fetch(self, query: FetchQuery) -> FetchResult:
        """Fetch papers from bioRxiv within the query's date window."""
        result = FetchResult(source=self._server)

        end = query.end_date or datetime.now(tz=timezone.utc)
        start = query.start_date or (end - timedelta(days=7))
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        logger.info(
            "%s fetch: %s → %s (max=%d)", self._server, start_str, end_str, query.max_results
        )

        cursor = 0
        max_pages = max(1, query.max_results // 100)  # scan at most 1 page per 100 requested
        pages_fetched = 0
        while len(result.papers) < query.max_results and pages_fetched < max_pages:
            self._throttle(self.rate_limits.biorxiv_delay_seconds)
            try:
                batch = self._fetch_page(start_str, end_str, cursor)
            except Exception as exc:
                logger.error("%s page fetch failed at cursor=%d: %s", self._server, cursor, exc)
                result.errors.append(str(exc))
                break

            if not batch:
                break

            for item in batch:
                paper = self._item_to_paper(item)
                if _matches_query(paper, query):
                    result.papers.append(paper)

            cursor += len(batch)
            pages_fetched += 1
            if len(batch) < 100:
                # Last page
                break

        logger.info("%s: collected %d papers", self._server, result.count)
        return result

    # ── Private ───────────────────────────────────────────────────────────

    def _fetch_page(self, start: str, end: str, cursor: int) -> list[dict[str, object]]:
        url = f"{_API_BASE}/{self._server}/{start}/{end}/{cursor}/json"
        response = self._client.get(url)
        response.raise_for_status()
        data: dict[str, object] = response.json()
        items = data.get("collection", [])
        return items if isinstance(items, list) else []  # type: ignore[return-value]

    def _item_to_paper(self, item: dict[str, object]) -> Paper:
        doi = str(item.get("doi", ""))
        date_str = str(item.get("date", ""))
        try:
            pub_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pub_date = datetime.now(tz=timezone.utc)

        author_str = str(item.get("authors", ""))
        authors = [Author(name=name.strip()) for name in author_str.split(";") if name.strip()]

        category = str(item.get("category", ""))
        version = str(item.get("version", "1"))
        pdf_url = f"https://www.biorxiv.org/content/{doi}v{version}.full.pdf" if doi else None

        paper_id = Paper.make_doi_id(doi) if doi else f"biorxiv:{item.get('rel_doi', '')}"

        return Paper(
            id=paper_id,
            title=str(item.get("title", "")).strip(),
            abstract=str(item.get("abstract", "")).strip() or None,
            authors=authors,
            categories=[category] if category else [],
            source=PaperSource.BIORXIV,
            venue=self._server,
            published_date=pub_date,
            updated_date=pub_date,
            pdf_url=pdf_url,
            doi=doi or None,
            raw_metadata={
                "server": self._server,
                "version": version,
                "type": item.get("type"),
                "license": item.get("license"),
            },
        )

    def close(self) -> None:
        self._client.close()


def _matches_query(paper: Paper, query: FetchQuery) -> bool:
    """Return True if the paper satisfies the bioRxiv category/keyword filter."""
    cats = query.biorxiv_categories  # use bioRxiv-specific categories, not arXiv ones
    if not cats and not query.keywords:
        return True

    cat_ok = True
    if cats:
        cat_ok = any(qc.lower() in pc.lower() for qc in cats for pc in paper.categories)

    if not query.keywords:
        return cat_ok

    text = f"{paper.title} {paper.abstract or ''}".lower()
    kw_ok = any(kw.lower() in text for kw in query.keywords)
    return cat_ok or kw_ok
