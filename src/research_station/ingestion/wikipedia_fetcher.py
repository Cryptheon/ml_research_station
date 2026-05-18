"""Wikipedia fetcher using the MediaWiki REST API.

Each Wikipedia article is stored as a Paper with:
  - id: wikipedia:{lang}:{Normalized_Title}  (underscores, Wikipedia canonical form)
  - abstract: lead section (intro before first == heading)
  - full text cached immediately to data/ocr/ so rag_query() works without OCR
  - CACHE_FULLTEXT flag set in DB after upsert

Articles have no PDF and no pdf_url — all text access goes through rag_query().
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ..config.settings import RateLimitSettings
from ..models.paper import CACHE_FULLTEXT, Paper, PaperSource
from .base import BaseFetcher, FetchQuery, FetchResult

logger = logging.getLogger(__name__)

_API = "https://{lang}.wikipedia.org/w/api.php"
_DEFAULT_DELAY = 0.5  # seconds between requests


def _normalize_title(title: str) -> str:
    """Convert a Wikipedia display title to its canonical underscore form."""
    return title.strip().replace(" ", "_")


def _make_paper_id(lang: str, title: str) -> str:
    return f"wikipedia:{lang}:{_normalize_title(title)}"


def _lead_section(text: str, max_chars: int = 2000) -> str:
    """Extract the lead section (before first == heading) as the abstract."""
    m = re.search(r"\n==\s", text)
    lead = text[: m.start()].strip() if m else text.strip()
    return lead[:max_chars]


class WikipediaFetcher(BaseFetcher):
    """Fetches Wikipedia articles matching search keywords."""

    source_name = "wikipedia"

    def __init__(self, rate_limits: RateLimitSettings, ocr_dir: Path | None = None) -> None:
        super().__init__(rate_limits)
        self._client = httpx.Client(
            timeout=20.0,
            headers={"User-Agent": "MeridianResearchStation/1.0 (research tool)"},
        )
        self._ocr_dir = ocr_dir  # set by pipeline so text can be cached immediately

    # ── Public ────────────────────────────────────────────────────────────

    def fetch(self, query: FetchQuery) -> FetchResult:
        result = FetchResult(source="wikipedia")
        languages = query.wikipedia_languages or ["en"]
        keywords = query.keywords or []

        if not keywords:
            return result  # nothing to search without keywords

        per_kw = max(5, query.max_results // (len(keywords) * len(languages)))
        seen: set[str] = set()

        for lang in languages:
            for kw in keywords:
                if len(result.papers) >= query.max_results:
                    break
                try:
                    self._throttle(_DEFAULT_DELAY)
                    titles = self._search(lang, kw, limit=per_kw)
                    for title in titles:
                        if len(result.papers) >= query.max_results:
                            break
                        pid = _make_paper_id(lang, title)
                        if pid in seen:
                            continue
                        seen.add(pid)
                        try:
                            self._throttle(_DEFAULT_DELAY)
                            paper = self._fetch_article(lang, title)
                            if paper:
                                result.papers.append(paper)
                        except Exception as exc:
                            logger.warning("wikipedia: article fetch failed %r: %s", title, exc)
                            result.errors.append(str(exc))
                except Exception as exc:
                    logger.error("wikipedia: search failed lang=%s kw=%r: %s", lang, kw, exc)
                    result.errors.append(str(exc))

        logger.info("wikipedia: collected %d articles", result.count)
        return result

    def fetch_by_title(self, lang: str, title: str) -> Paper | None:
        """Fetch a single article by exact title — used by the agent tool."""
        self._throttle(_DEFAULT_DELAY)
        return self._fetch_article(lang, title)

    def close(self) -> None:
        self._client.close()

    # ── Private ───────────────────────────────────────────────────────────

    def _search(self, lang: str, query: str, limit: int) -> list[str]:
        """Return a list of page titles matching *query* in *lang* Wikipedia."""
        resp = self._client.get(
            _API.format(lang=lang),
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": min(limit, 50),
                "srwhat": "text",
                "format": "json",
            },
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        hits = data.get("query", {}).get("search", [])
        return [h["title"] for h in hits if "title" in h]

    def _fetch_article(self, lang: str, title: str) -> Paper | None:
        """Fetch full article text and metadata; returns None if page missing."""
        resp = self._client.get(
            _API.format(lang=lang),
            params={
                "action": "query",
                "prop": "extracts|info|categories",
                "titles": title,
                "explaintext": "1",
                "exsectionformat": "plain",
                "inprop": "url|touched",
                "cllimit": "20",
                "format": "json",
                "redirects": "1",
            },
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()

        pages: dict[str, Any] = data.get("query", {}).get("pages", {})
        if not pages:
            return None

        page = next(iter(pages.values()))
        if page.get("missing") is not None:
            return None

        resolved_title: str = page.get("title", title)
        full_text: str = page.get("extract", "").strip()
        if not full_text:
            return None

        touched = page.get("touched", "")  # e.g. "2024-01-15T12:00:00Z"
        try:
            pub_date = datetime.strptime(touched[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            pub_date = datetime.now(tz=timezone.utc)

        cats = [
            c["title"].removeprefix("Category:") for c in page.get("categories", []) if "title" in c
        ]
        page_url = page.get(
            "fullurl", f"https://{lang}.wikipedia.org/wiki/{_normalize_title(resolved_title)}"
        )
        paper_id = _make_paper_id(lang, resolved_title)
        abstract = _lead_section(full_text)

        # Cache full text immediately so rag_query() works without a separate step
        self._cache_text(paper_id, full_text)

        return Paper(
            id=paper_id,
            title=resolved_title,
            abstract=abstract,
            authors=[],
            categories=cats[:10],
            keywords=[],
            source=PaperSource.WIKIPEDIA,
            venue=f"Wikipedia ({lang})",
            published_date=pub_date,
            updated_date=pub_date,
            pdf_url=None,
            doi=None,
            is_downloaded=False,
            raw_metadata={"lang": lang, "page_url": page_url, "pageid": page.get("pageid")},
            cache_flags=CACHE_FULLTEXT,
        )

    def _cache_text(self, paper_id: str, text: str) -> None:
        if self._ocr_dir is None:
            return
        try:
            safe = paper_id.replace(":", "_").replace("/", "_").replace(" ", "_")
            txt_path = self._ocr_dir / f"{safe}.txt"
            txt_path.parent.mkdir(parents=True, exist_ok=True)
            txt_path.write_text(text, encoding="utf-8")
        except Exception as exc:
            logger.warning("wikipedia: could not cache text for %s: %s", paper_id, exc)
