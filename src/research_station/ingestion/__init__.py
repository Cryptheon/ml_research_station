"""Paper ingestion: fetchers, enrichment, PDF download, and pipeline orchestration."""

from .arxiv_fetcher import ArxivFetcher
from .base import BaseFetcher, FetchQuery, FetchResult
from .biorxiv_fetcher import BiorxivFetcher
from .openreview_fetcher import OpenReviewFetcher
from .pdf_downloader import PDFDownloader
from .pipeline import IngestionPipeline, PipelineResult
from .semantic_scholar import SemanticScholarClient

__all__ = [
    "ArxivFetcher",
    "BaseFetcher",
    "BiorxivFetcher",
    "FetchQuery",
    "FetchResult",
    "IngestionPipeline",
    "OpenReviewFetcher",
    "PDFDownloader",
    "PipelineResult",
    "SemanticScholarClient",
]
