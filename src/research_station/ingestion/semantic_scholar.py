"""Semantic Scholar Graph API client.

Used for two purposes:
1. **Enrichment**: merge citation counts, TLDR, and open-access PDF URL into
   papers already fetched from arXiv/bioRxiv/OpenReview.
2. **Citation graph**: pull outgoing references and incoming citations for
   building the knowledge graph.

API reference: https://api.semanticscholar.org/api-docs/graph
Rate limits:
  - No key:  1 req/s, 5 000 req/day
  - With key: 10 req/s, 100 000 req/day  (register free at the link above)

Design choices:
- All IDs passed to the API use the ``ArXiv:`` / ``DOI:`` prefix so S2 can
  resolve them without knowing internal S2 paper IDs first.
- ``enrich_paper`` never overwrites non-null fields; it only fills gaps.
- Citation fetching is paginated (max 100 per request) and stops at the first
  empty page rather than checking a ``next`` token, which is simpler and safe
  for our use-case.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config.settings import RateLimitSettings
from ..models.paper import Author, Citation, Paper, PaperSource

logger = logging.getLogger(__name__)

_S2_BASE = "https://api.semanticscholar.org/graph/v1"

_PAPER_FIELDS = ",".join(
    [
        "paperId",
        "externalIds",
        "title",
        "abstract",
        "authors",
        "year",
        "publicationDate",
        "venue",
        "publicationVenue",
        "citationCount",
        "referenceCount",
        "influentialCitationCount",
        "fieldsOfStudy",
        "s2FieldsOfStudy",
        "openAccessPdf",
        "tldr",
        "isOpenAccess",
    ]
)

_EDGE_FIELDS = "paperId,title,authors,year,isInfluential,contexts"


class SemanticScholarClient:
    """Thin wrapper around the Semantic Scholar Graph API."""

    def __init__(self, api_key: str | None, rate_limits: RateLimitSettings) -> None:
        self._rate_limits = rate_limits
        self._last_request: float = 0.0
        headers: dict[str, str] = {}
        if api_key:
            headers["x-api-key"] = api_key
        self._client = httpx.Client(headers=headers, timeout=30.0)

    # ── Public interface ──────────────────────────────────────────────────

    def enrich_paper(self, paper: Paper) -> Paper:
        """Fetch S2 metadata and merge it into *paper* (fills nulls only)."""
        s2_id = self._resolve_id(paper)
        if not s2_id:
            logger.debug("No resolvable S2 ID for: %.60s", paper.title)
            return paper
        data = self._get_paper(s2_id)
        return self._merge(paper, data) if data else paper

    def get_references(self, paper: Paper) -> list[Citation]:
        """Return outgoing citation edges (papers *paper* cites)."""
        s2_id = paper.semantic_scholar_id
        if not s2_id:
            return []
        return self._fetch_edges(s2_id, direction="references")

    def get_incoming_citations(self, paper: Paper) -> list[Citation]:
        """Return incoming citation edges (papers that cite *paper*)."""
        s2_id = paper.semantic_scholar_id
        if not s2_id:
            return []
        return self._fetch_edges(s2_id, direction="citations")

    def batch_lookup_by_arxiv(self, arxiv_ids: list[str]) -> list[Paper]:
        """Bulk-fetch S2 metadata for a list of arXiv IDs (max ~500/call)."""
        if not arxiv_ids:
            return []
        self._throttle()
        try:
            response = self._client.post(
                f"{_S2_BASE}/paper/batch",
                params={"fields": _PAPER_FIELDS},
                json={"ids": [f"ArXiv:{aid}" for aid in arxiv_ids]},
            )
            response.raise_for_status()
            return [self._data_to_paper(d) for d in response.json() if d is not None]
        except Exception as exc:
            logger.error("S2 batch lookup failed: %s", exc)
            return []

    def close(self) -> None:
        self._client.close()

    # ── Private ───────────────────────────────────────────────────────────

    def _resolve_id(self, paper: Paper) -> str | None:
        """Return the best S2-compatible external ID string for *paper*."""
        if paper.semantic_scholar_id:
            return paper.semantic_scholar_id
        if paper.arxiv_id:
            return f"ArXiv:{paper.arxiv_id}"
        if paper.doi:
            return f"DOI:{paper.doi}"
        return None

    def _get_paper(self, s2_id: str) -> dict[str, Any] | None:
        self._throttle()
        try:
            response = self._client.get(
                f"{_S2_BASE}/paper/{s2_id}",
                params={"fields": _PAPER_FIELDS},
            )
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            logger.warning("S2 paper fetch HTTP %d for %s", exc.response.status_code, s2_id)
            return None
        except Exception as exc:
            logger.error("S2 paper fetch error for %s: %s", s2_id, exc)
            return None

    def _fetch_edges(self, s2_id: str, direction: str) -> list[Citation]:
        """Paginated fetch of citation or reference edges."""
        results: list[Citation] = []
        offset = 0
        limit = 100

        while True:
            self._throttle()
            try:
                response = self._client.get(
                    f"{_S2_BASE}/paper/{s2_id}/{direction}",
                    params={"fields": _EDGE_FIELDS, "offset": offset, "limit": limit},
                )
                response.raise_for_status()
                data: dict[str, Any] = response.json()
            except Exception as exc:
                logger.error("S2 %s fetch failed for %s: %s", direction, s2_id, exc)
                break

            items: list[dict[str, Any]] = data.get("data", [])
            for item in items:
                edge_paper = item.get("citedPaper") or item.get("citingPaper") or {}
                peer_s2_id: str | None = edge_paper.get("paperId")
                if not peer_s2_id:
                    continue

                citing_id = Paper.make_s2_id(s2_id)
                cited_id = Paper.make_s2_id(peer_s2_id)

                contexts: list[str] = item.get("contexts") or []
                results.append(
                    Citation(
                        citing_paper_id=citing_id if direction == "references" else cited_id,
                        cited_paper_id=cited_id if direction == "references" else citing_id,
                        context=contexts[0] if contexts else None,
                        is_influential=bool(item.get("isInfluential", False)),
                    )
                )

            if len(items) < limit:
                break
            offset += limit

        return results

    @staticmethod
    def _merge(paper: Paper, data: dict[str, Any]) -> Paper:
        """Return a copy of *paper* with S2 fields applied where missing."""
        updates: dict[str, Any] = {}

        if data.get("paperId"):
            updates["semantic_scholar_id"] = data["paperId"]
        if data.get("citationCount") is not None:
            updates["citation_count"] = data["citationCount"]
        if data.get("referenceCount") is not None:
            updates["reference_count"] = data["referenceCount"]
        if data.get("influentialCitationCount") is not None:
            updates["influential_citation_count"] = data["influentialCitationCount"]

        tldr = data.get("tldr")
        if tldr and isinstance(tldr, dict) and not paper.tldr:
            updates["tldr"] = tldr.get("text")

        if not paper.abstract and data.get("abstract"):
            updates["abstract"] = data["abstract"]
        if not paper.venue and data.get("venue"):
            updates["venue"] = data["venue"]

        oa_pdf = data.get("openAccessPdf")
        if not paper.pdf_url and oa_pdf and isinstance(oa_pdf, dict):
            updates["pdf_url"] = oa_pdf.get("url")

        return paper.model_copy(update=updates)

    @staticmethod
    def _data_to_paper(data: dict[str, Any]) -> Paper:
        """Convert a raw S2 API record to a Paper model."""
        external: dict[str, str] = data.get("externalIds") or {}
        arxiv_id = external.get("ArXiv")
        doi = external.get("DOI")

        paper_id = (
            Paper.make_arxiv_id(arxiv_id)
            if arxiv_id
            else Paper.make_doi_id(doi)
            if doi
            else Paper.make_s2_id(data["paperId"])
        )

        pub_date_str: str | None = data.get("publicationDate")
        year: int = data.get("year") or 2000
        pub_date = (
            datetime.strptime(pub_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if pub_date_str
            else datetime(year, 1, 1, tzinfo=timezone.utc)
        )

        authors = [Author(name=str(a.get("name", ""))) for a in (data.get("authors") or [])]

        oa_pdf = data.get("openAccessPdf")
        pdf_url = oa_pdf.get("url") if isinstance(oa_pdf, dict) else None

        tldr = data.get("tldr")
        tldr_text = tldr.get("text") if isinstance(tldr, dict) else None

        return Paper(
            id=paper_id,
            title=str(data.get("title", "")),
            abstract=data.get("abstract"),
            authors=authors,
            categories=[str(f.get("category", "")) for f in (data.get("s2FieldsOfStudy") or [])],
            source=PaperSource.SEMANTIC_SCHOLAR,
            venue=data.get("venue"),
            published_date=pub_date,
            updated_date=pub_date,
            doi=doi,
            arxiv_id=arxiv_id,
            semantic_scholar_id=data.get("paperId"),
            citation_count=data.get("citationCount"),
            reference_count=data.get("referenceCount"),
            influential_citation_count=data.get("influentialCitationCount"),
            tldr=tldr_text,
            pdf_url=pdf_url,
            raw_metadata={"s2_fields": data.get("fieldsOfStudy") or []},
        )

    def _throttle(self) -> None:
        delay = self._rate_limits.semantic_scholar_delay_seconds
        elapsed = time.monotonic() - self._last_request
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request = time.monotonic()
