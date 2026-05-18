"""Tests for ArxivFetcher.

Unit tests use a mock arxiv.Client; integration tests hit the live API
and are skipped in CI (mark: ``integration``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from research_station.config.settings import RateLimitSettings
from research_station.ingestion.arxiv_fetcher import ArxivFetcher
from research_station.ingestion.base import FetchQuery
from research_station.models.paper import PaperSource


@pytest.fixture
def rate_limits() -> RateLimitSettings:
    return RateLimitSettings(arxiv_delay_seconds=0.1)


@pytest.fixture
def fetcher(rate_limits: RateLimitSettings) -> ArxivFetcher:
    return ArxivFetcher(rate_limits)


# ── Unit tests ────────────────────────────────────────────────────────────────


class TestQueryBuilder:
    def test_categories_only(self, fetcher: ArxivFetcher) -> None:
        query = FetchQuery(categories=["cs.LG", "cs.AI"])
        q = fetcher._build_query_string(query)
        assert "cat:cs.LG" in q
        assert "cat:cs.AI" in q

    def test_keywords_only(self, fetcher: ArxivFetcher) -> None:
        query = FetchQuery(keywords=["diffusion model"])
        q = fetcher._build_query_string(query)
        assert 'ti:"diffusion model"' in q
        assert 'abs:"diffusion model"' in q

    def test_empty_query_defaults_to_cs_lg(self, fetcher: ArxivFetcher) -> None:
        query = FetchQuery()
        assert fetcher._build_query_string(query) == "cat:cs.LG"

    def test_combined_uses_and(self, fetcher: ArxivFetcher) -> None:
        query = FetchQuery(categories=["cs.CV"], keywords=["ViT"])
        q = fetcher._build_query_string(query)
        assert " AND " in q


class TestDateFilter:
    def _make_paper_on(self, days_ago: int) -> MagicMock:
        from research_station.models.paper import Paper

        pub = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
        paper = MagicMock(spec=Paper)
        paper.published_date = pub
        return paper

    def test_passes_within_window(self, fetcher: ArxivFetcher) -> None:
        from research_station.ingestion.arxiv_fetcher import _passes_date_filter

        query = FetchQuery(
            start_date=datetime.now(tz=timezone.utc) - timedelta(days=7),
        )
        paper = self._make_paper_on(3)
        assert _passes_date_filter(paper, query)

    def test_fails_outside_window(self, fetcher: ArxivFetcher) -> None:
        from research_station.ingestion.arxiv_fetcher import _passes_date_filter

        query = FetchQuery(
            start_date=datetime.now(tz=timezone.utc) - timedelta(days=7),
        )
        paper = self._make_paper_on(14)
        assert not _passes_date_filter(paper, query)


# ── Integration tests (live arXiv API) ───────────────────────────────────────


@pytest.mark.integration
class TestArxivFetcherIntegration:
    def test_fetch_recent_cs_lg(self, fetcher: ArxivFetcher) -> None:
        query = FetchQuery(
            categories=["cs.LG"],
            max_results=5,
            start_date=datetime.now(tz=timezone.utc) - timedelta(days=30),
        )
        result = fetcher.fetch(query)
        assert result.success, result.errors
        assert result.count > 0
        assert all(p.source == PaperSource.ARXIV for p in result.papers)
        assert all(p.arxiv_id is not None for p in result.papers)
        assert all(p.id.startswith("arxiv:") for p in result.papers)

    def test_canonical_id_format(self, fetcher: ArxivFetcher) -> None:
        query = FetchQuery(categories=["cs.AI"], max_results=2)
        result = fetcher.fetch(query)
        for paper in result.papers:
            # Must not contain version suffix
            assert "v" not in paper.id.split("arxiv:")[1] or paper.id.count("v") == 0

    def test_keyword_search(self, fetcher: ArxivFetcher) -> None:
        query = FetchQuery(
            categories=["cs.LG"],
            keywords=["attention"],
            max_results=3,
        )
        result = fetcher.fetch(query)
        assert result.count > 0
