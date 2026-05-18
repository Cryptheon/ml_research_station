"""Domain models: Pydantic schemas and SQLAlchemy ORM definitions."""

from .paper import (
    Author,
    Citation,
    CitationORM,
    Paper,
    PaperORM,
    PaperScores,
    PaperSource,
    PaperStatus,
)
from .summary import PaperSummary, PaperSummaryORM
from .taxonomy import TOPICS
from .taxonomy import classify as classify_topics
from .user import Collection, CollectionItem, IngestHistory, Pin, User, Watch

__all__ = [
    "TOPICS",
    "Author",
    "Citation",
    "CitationORM",
    "Collection",
    "CollectionItem",
    "IngestHistory",
    "Paper",
    "PaperORM",
    "PaperScores",
    "PaperSource",
    "PaperStatus",
    "PaperSummary",
    "PaperSummaryORM",
    "Pin",
    "User",
    "Watch",
    "classify_topics",
]
