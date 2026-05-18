"""API response schemas.

These match the shape that the frontend (frontend/) expects.
The internal Paper model uses snake_case and datetime objects; these schemas
reshape the data into camelCase-ish flat dicts that the JS components consume.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..models.paper import Paper

# ── Paper shapes ──────────────────────────────────────────────────────────────

_SOURCE_DISPLAY = {
    "arxiv": "arXiv",
    "biorxiv": "bioRxiv",
    "openreview": "OpenReview",
    "semantic_scholar": "Semantic Scholar",
    "wikipedia": "Wikipedia",
    "web": "Web",
}


class ScoresOut(BaseModel):
    relevance: float
    novelty: float
    velocity: float


class PaperCard(BaseModel):
    """Minimal paper representation used in queue / list views."""

    id: str
    title: str
    authors: list[str]
    venue: str | None
    year: int
    date: str
    source: str
    topics: list[str]
    scores: ScoresOut
    citedBy: int
    references: int
    abstract: str | None
    pinned: bool
    status: str
    created_at: str
    arxiv_id: str | None
    doi: str | None
    pdf_url: str | None
    categories: list[str]
    is_downloaded: bool
    cache_flags: int

    @classmethod
    def from_paper(cls, paper: Paper) -> PaperCard:
        return cls(
            id=paper.id,
            title=paper.title,
            authors=[a.name for a in paper.authors],
            venue=paper.venue,
            year=paper.published_date.year,
            date=paper.published_date.date().isoformat(),
            source=_SOURCE_DISPLAY.get(paper.source.value, paper.source.value),
            topics=paper.topics,
            scores=ScoresOut(
                relevance=paper.scores.relevance,
                novelty=paper.scores.novelty,
                velocity=paper.scores.velocity,
            ),
            citedBy=paper.citation_count or 0,
            references=paper.reference_count or 0,
            abstract=paper.abstract,
            pinned=paper.pinned,
            status=paper.status.value,
            created_at=paper.created_at.isoformat() if paper.created_at else "",
            arxiv_id=paper.arxiv_id,
            doi=paper.doi,
            pdf_url=paper.pdf_url,
            categories=paper.categories,
            is_downloaded=paper.is_downloaded,
            cache_flags=paper.cache_flags,
        )


class PaperDetail(PaperCard):
    """Full paper detail including all metadata fields."""

    semantic_scholar_id: str | None
    openreview_id: str | None
    tldr: str | None
    velocity_12w: list[int]
    influential_citation_count: int | None
    updated_date: str

    @classmethod
    def from_paper(cls, paper: Paper) -> PaperDetail:  # type: ignore[override]
        card = PaperCard.from_paper(paper)
        return cls(
            **card.model_dump(),
            semantic_scholar_id=paper.semantic_scholar_id,
            openreview_id=paper.openreview_id,
            tldr=paper.tldr,
            velocity_12w=paper.velocity_12w,
            influential_citation_count=paper.influential_citation_count,
            updated_date=paper.updated_date.date().isoformat(),
        )


# ── Filter / query shapes ─────────────────────────────────────────────────────


class FilterSpec(BaseModel):
    """Query parameters for the papers/queue endpoint."""

    q: str = ""
    sources: list[str] = []
    topics: list[str] = []
    venues: list[str] = []
    status: list[str] = []
    pinned: bool | None = None
    since_days: int | None = None
    sort: str = "date"
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


# ── System shapes ─────────────────────────────────────────────────────────────


class ServiceKey(BaseModel):
    name: str
    env_var: str
    present: bool
    optional: bool = False
    hint: str = ""


class IngestPrefs(BaseModel):
    max_results_per_source: int
    days_lookback: int
    arxiv_categories: list[str]
    biorxiv_categories: list[str]
    wikipedia_languages: list[str] = ["en"]


class ConfigOut(BaseModel):
    keys: list[ServiceKey]
    llm_provider: str
    llm_model: str
    llm_base_url: str | None
    llm_temperature: float
    llm_max_tokens: int
    llm_top_p: float | None
    llm_top_k: int | None
    llm_repetition_penalty: float | None
    llm_presence_penalty: float | None
    llm_enable_thinking: bool
    ocr_provider: str | None
    ocr_model: str | None
    ocr_base_url: str | None
    ocr_max_tokens: int | None
    ocr_dpi: int | None
    ocr_semaphore_limit: int | None
    ocr_backend: str | None
    ocr_use_ngram_processor: bool | None
    ocr_repetition_penalty: float | None
    ocr_text_extract: bool
    embed_provider: str
    embed_model: str
    embed_vllm_base_url: str
    embed_ollama_base_url: str
    env_file: str
    prefs: IngestPrefs
    agent_strip_parallel_tool_calls: bool = True


class HealthOut(BaseModel):
    status: str = "ok"
    paper_count: int
    citation_count: int
    summary_count: int
    db_size_mb: float
    llm_provider: str
    llm_model: str
    version: str = "0.3.0"


class SourceOut(BaseModel):
    id: str
    display_name: str
    active: bool = True
    locked: bool = False
    lock_reason: str | None = None


class TaxonomyLane(BaseModel):
    id: str
    label: str


# ── Citation shapes ───────────────────────────────────────────────────────────


class CitationEdge(BaseModel):
    from_id: str
    to_id: str
    is_influential: bool


class CitationGraph(BaseModel):
    nodes: list[PaperCard]
    edges: list[CitationEdge]


# ── Stats / velocity ──────────────────────────────────────────────────────────


class VelocityOut(BaseModel):
    paper_id: str
    cited_by: int
    velocity_12w: list[int]
    cites_delta: int
    rank: int | None = None
    percentile: float | None = None
