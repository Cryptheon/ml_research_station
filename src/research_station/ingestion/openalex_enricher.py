"""OpenAlex Works API client — free enrichment source, no API key required.

Used as enrichment alternative (or complement) to Semantic Scholar:
1. **Enrichment**: fills citation counts, abstract, open-access PDF URL,
   and 12-week citation velocity from `counts_by_year`.
2. **References**: maps `referenced_works` OpenAlex IDs to Citation edges.

API reference: https://docs.openalex.org/api-entities/works
Rate limits: ~100 req/s in polite pool (requires User-Agent with email).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..models.paper import Citation, Paper

logger = logging.getLogger(__name__)

_OA_BASE = "https://api.openalex.org"
_CONTACT_EMAIL = "brycargue@gmail.com"
_USER_AGENT = f"ResearchStation/0.3 (contact: {_CONTACT_EMAIL})"


class OpenAlexClient:
    """Thin wrapper around the OpenAlex Works API."""

    def __init__(self, delay_seconds: float = 0.1) -> None:
        self._delay = delay_seconds
        self._last_request: float = 0.0
        self._client = httpx.Client(
            timeout=30.0,
            headers={
                "User-Agent": _USER_AGENT,
                "mailto": _CONTACT_EMAIL,
            },
        )

    # ── Public interface ──────────────────────────────────────────────────

    def enrich_paper(self, paper: Paper) -> Paper:
        """Fetch OpenAlex metadata and merge into *paper* (fills nulls only)."""
        data = self._resolve(paper)
        if not data:
            logger.debug("OpenAlex: no record found for %.60s", paper.title)
            return paper
        return self._merge(paper, data)

    def get_references(self, paper: Paper) -> list[Citation]:
        """Return outgoing citation edges by fetching referenced_works detail."""
        data = self._resolve(paper)
        if not data:
            return []
        return self._extract_references(paper.id, data)

    def close(self) -> None:
        self._client.close()

    # ── Private ───────────────────────────────────────────────────────────

    def _resolve(self, paper: Paper) -> dict[str, Any] | None:
        """Try arXiv ID first, then DOI."""
        if paper.arxiv_id:
            data = self._get_work(f"https://arxiv.org/abs/{paper.arxiv_id}")
            if data:
                return data
        if paper.doi:
            data = self._get_work(f"https://doi.org/{paper.doi}")
            if data:
                return data
        return None

    def _get_work(self, external_id_url: str) -> dict[str, Any] | None:
        self._throttle()
        try:
            response = self._client.get(
                f"{_OA_BASE}/works/{external_id_url}",
                params={
                    "select": ",".join(
                        [
                            "id",
                            "doi",
                            "ids",
                            "title",
                            "abstract_inverted_index",
                            "cited_by_count",
                            "referenced_works_count",
                            "counts_by_year",
                            "referenced_works",
                            "open_access",
                            "best_oa_location",
                            "primary_location",
                            "authorships",
                            "publication_year",
                            "publication_date",
                        ]
                    )
                },
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            logger.warning("OpenAlex HTTP %d for %s", exc.response.status_code, external_id_url)
            return None
        except Exception as exc:
            logger.error("OpenAlex fetch error for %s: %s", external_id_url, exc)
            return None

    @staticmethod
    def _merge(paper: Paper, data: dict[str, Any]) -> Paper:
        """Return copy of *paper* with OpenAlex fields applied where missing."""
        updates: dict[str, Any] = {}

        if data.get("cited_by_count") is not None and paper.citation_count is None:
            updates["citation_count"] = data["cited_by_count"]

        if data.get("referenced_works_count") is not None and paper.reference_count is None:
            updates["reference_count"] = data["referenced_works_count"]

        # velocity_12w from counts_by_year — most recent 12 months as weekly buckets
        if not paper.velocity_12w:
            velocity = _counts_by_year_to_velocity(data.get("counts_by_year") or [])
            if velocity:
                updates["velocity_12w"] = velocity

        # Abstract reconstruction from inverted index
        if not paper.abstract:
            abstract = _reconstruct_abstract(data.get("abstract_inverted_index"))
            if abstract:
                updates["abstract"] = abstract

        # Open-access PDF
        if not paper.pdf_url:
            pdf_url = _extract_pdf_url(data)
            if pdf_url:
                updates["pdf_url"] = pdf_url

        return paper.model_copy(update=updates)

    @staticmethod
    def _extract_references(paper_id: str, data: dict[str, Any]) -> list[Citation]:
        """Build Citation edges from referenced_works OpenAlex IDs."""
        refs: list[Citation] = []
        for oa_url in data.get("referenced_works") or []:
            # OpenAlex IDs look like "https://openalex.org/W1234567890"
            oa_id = oa_url.split("/")[-1] if "/" in oa_url else oa_url
            if not oa_id:
                continue
            cited_id = f"openalex:{oa_id}"
            refs.append(
                Citation(
                    citing_paper_id=paper_id,
                    cited_paper_id=cited_id,
                    is_influential=False,
                )
            )
        return refs

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)
        self._last_request = time.monotonic()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """Reconstruct abstract string from OpenAlex inverted index format."""
    if not inverted_index:
        return None
    positions: list[tuple[int, str]] = []
    for word, locs in inverted_index.items():
        for pos in locs:
            positions.append((pos, word))
    if not positions:
        return None
    positions.sort(key=lambda x: x[0])
    return " ".join(w for _, w in positions)


def _counts_by_year_to_velocity(counts_by_year: list[dict[str, Any]]) -> list[int]:
    """Map OpenAlex `counts_by_year` to a 12-element weekly-bucket list.

    OpenAlex gives annual totals; we spread each year evenly into 52 weekly
    buckets and return the most recent 12 weekly buckets (≈ last quarter).
    """
    if not counts_by_year:
        return []

    # Sort descending by year, take up to 3 years for signal
    by_year = sorted(counts_by_year, key=lambda r: r.get("year", 0), reverse=True)

    # Build a per-week list across the last ~3 years (156 weeks) then slice last 12
    weekly: list[float] = []
    for record in by_year[:3]:
        annual = record.get("cited_by_count", 0) or 0
        per_week = annual / 52.0
        # Prepend 52 weeks for this year
        weekly = [per_week] * 52 + weekly

    # Return last 12 weeks as integers
    return [round(w) for w in weekly[-12:]] if len(weekly) >= 12 else []


def _extract_pdf_url(data: dict[str, Any]) -> str | None:
    """Extract best open-access PDF URL from OpenAlex work record."""
    oa = data.get("open_access") or {}
    if oa.get("oa_url"):
        url: str = oa["oa_url"]
        if url.endswith(".pdf") or "pdf" in url:
            return url

    best = data.get("best_oa_location") or {}
    pdf = best.get("pdf_url")
    if pdf:
        return pdf

    primary = data.get("primary_location") or {}
    return primary.get("pdf_url")
