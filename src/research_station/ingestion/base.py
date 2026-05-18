"""Abstract base class and shared data structures for paper fetchers."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from ..config.settings import RateLimitSettings
from ..models.paper import Paper


@dataclass
class FetchQuery:
    """Parameters controlling a single fetch operation.

    All fields are optional so callers can mix-and-match; fetchers adapt
    to whichever fields they support (e.g. OpenReview ignores ``categories``).
    """

    keywords: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)  # arXiv categories
    biorxiv_categories: list[str] = field(default_factory=list)  # bioRxiv categories
    wikipedia_languages: list[str] = field(default_factory=lambda: ["en"])
    venues: list[str] = field(default_factory=list)
    start_date: datetime | None = None
    end_date: datetime | None = None
    max_results: int = 100


@dataclass
class FetchResult:
    """Outcome of a single fetch operation."""

    papers: list[Paper] = field(default_factory=list)
    source: str = ""
    fetched_at: datetime = field(default_factory=datetime.utcnow)
    errors: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.papers)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


class BaseFetcher(ABC):
    """Contract that every source-specific fetcher must satisfy.

    Subclasses must set ``source_name`` and implement ``fetch()``.
    The ``_throttle`` helper enforces the per-source delay without each
    fetcher needing its own timing logic.
    """

    source_name: str = ""

    def __init__(self, rate_limits: RateLimitSettings) -> None:
        self.rate_limits = rate_limits
        self._last_request_time: float = 0.0

    @abstractmethod
    def fetch(self, query: FetchQuery) -> FetchResult:
        """Fetch papers matching *query* from this source."""
        ...

    def _throttle(self, delay_seconds: float) -> None:
        """Block until *delay_seconds* have elapsed since the last request."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < delay_seconds:
            time.sleep(delay_seconds - elapsed)
        self._last_request_time = time.monotonic()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(source={self.source_name!r})"
