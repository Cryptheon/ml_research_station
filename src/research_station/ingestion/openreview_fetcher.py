"""OpenReview fetcher for NeurIPS, ICLR, ICML, and TMLR.

OpenReview hosts the official review systems for most top ML conferences.
Guest (unauthenticated) access is sufficient for reading accepted-paper notes.

API used: OpenReview API v2 (api2.openreview.net) via openreview-py >= 1.14.

Design choices:
- Venue→invitation mappings live in ``VENUE_INVITATIONS``; add new years /
  venues there without touching fetcher logic.
- The Note content schema varies slightly between venues (some use nested
  ``{"value": ...}`` objects, others plain strings); ``_get_value`` handles both.
- We do NOT filter by acceptance decision here — that logic varies per venue
  and is better applied as a post-processing step once we have the full note
  with decision replies.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import openreview

from ..config.settings import RateLimitSettings
from ..models.paper import Author, Paper, PaperSource
from .base import BaseFetcher, FetchQuery, FetchResult

logger = logging.getLogger(__name__)

# Extend this dict as new conference years become available on OpenReview.
VENUE_INVITATIONS: dict[str, list[str]] = {
    "ICLR": [
        "ICLR.cc/2025/Conference/-/Submission",
        "ICLR.cc/2024/Conference/-/Submission",
    ],
    "NeurIPS": [
        "NeurIPS.cc/2024/Conference/-/Submission",
    ],
    "ICML": [
        "ICML.cc/2024/Conference/-/Submission",
    ],
    "TMLR": [
        "TMLR/-/Submission",
    ],
}


class OpenReviewFetcher(BaseFetcher):
    """Fetches papers from OpenReview venues using the v2 API."""

    source_name = "openreview"

    def __init__(
        self,
        rate_limits: RateLimitSettings,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        super().__init__(rate_limits)
        self._client = openreview.api.OpenReviewClient(
            baseurl="https://api2.openreview.net",
            username=username,
            password=password,
        )

    def fetch(self, query: FetchQuery) -> FetchResult:
        """Fetch papers from the venues listed in *query.venues*."""
        result = FetchResult(source=self.source_name)
        target_venues = query.venues if query.venues else list(VENUE_INVITATIONS.keys())

        for venue in target_venues:
            if venue not in VENUE_INVITATIONS:
                logger.warning("Unknown OpenReview venue: %s — skipping", venue)
                continue

            for invitation in VENUE_INVITATIONS[venue]:
                self._throttle(self.rate_limits.openreview_delay_seconds)
                try:
                    papers = self._fetch_invitation(invitation, venue, query)
                    result.papers.extend(papers)
                    logger.info("OpenReview %s (%s): %d papers", venue, invitation, len(papers))
                except Exception as exc:
                    msg = f"{venue}/{invitation}: {exc}"
                    logger.error("OpenReview fetch failed — %s", msg)
                    result.errors.append(msg)

                if len(result.papers) >= query.max_results:
                    return result

        logger.info("OpenReview: collected %d papers total", result.count)
        return result

    # ── Private ───────────────────────────────────────────────────────────

    def _fetch_invitation(self, invitation: str, venue_name: str, query: FetchQuery) -> list[Paper]:
        notes = self._client.get_notes(invitation=invitation, limit=query.max_results)
        papers: list[Paper] = []
        for note in notes:
            try:
                paper = self._note_to_paper(note, venue_name)
                if _keyword_match(paper, query):
                    papers.append(paper)
            except Exception as exc:
                logger.debug("Skipping note %s: %s", getattr(note, "id", "?"), exc)
        return papers

    @staticmethod
    def _note_to_paper(note: openreview.Note, venue_name: str) -> Paper:
        content: dict[str, object] = note.content or {}

        def get_value(key: str) -> object:
            val = content.get(key)
            if isinstance(val, dict):
                return val.get("value")
            return val

        title = str(get_value("title") or "").strip()
        abstract_raw = get_value("abstract")
        abstract = str(abstract_raw).strip() if abstract_raw else None

        keywords_raw = get_value("keywords")
        if isinstance(keywords_raw, list):
            keywords: list[str] = [str(k) for k in keywords_raw]
        else:
            keywords = []

        authors_raw = get_value("authors")
        if isinstance(authors_raw, list):
            authors = [Author(name=str(n)) for n in authors_raw]
        else:
            authors = []

        cdate = getattr(note, "cdate", None)
        created = (
            datetime.fromtimestamp(cdate / 1000, tz=timezone.utc)
            if cdate
            else datetime.now(tz=timezone.utc)
        )

        forum_id: str = str(getattr(note, "forum", None) or note.id)

        return Paper(
            id=Paper.make_openreview_id(forum_id),
            title=title,
            abstract=abstract,
            authors=authors,
            categories=[venue_name],
            keywords=keywords,
            source=PaperSource.OPENREVIEW,
            venue=venue_name,
            published_date=created,
            updated_date=created,
            pdf_url=f"https://openreview.net/pdf?id={forum_id}",
            openreview_id=forum_id,
            raw_metadata={
                "invitation": getattr(note, "invitation", None),
                "note_id": str(note.id),
            },
        )


def _keyword_match(paper: Paper, query: FetchQuery) -> bool:
    if not query.keywords:
        return True
    text = f"{paper.title} {paper.abstract or ''}".lower()
    return any(kw.lower() in text for kw in query.keywords)
