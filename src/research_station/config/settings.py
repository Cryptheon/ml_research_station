"""Application settings using Pydantic Settings v2.

All values can be overridden via environment variables using the __ delimiter
for nested models (e.g. PREFERENCES__DAYS_LOOKBACK=14).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ResearchPreferences(BaseModel):
    """What to fetch and how much of it."""

    arxiv_categories: list[str] = Field(
        default=[
            "cs.LG",  # Machine Learning
            "cs.AI",  # Artificial Intelligence
            "cs.CV",  # Computer Vision
            "cs.CL",  # Computation and Language (NLP)
            "cs.NE",  # Neural and Evolutionary Computing
            "stat.ML",  # Statistics — Machine Learning
            "q-bio.QM",  # Quantitative Methods (bio ML)
        ],
        description="arXiv category codes to monitor.",
    )
    keywords: list[str] = Field(
        default=[],
        description="Free-text keywords searched in title+abstract.",
    )
    venues: list[str] = Field(
        default=["ICLR", "NeurIPS", "ICML", "TMLR"],
        description="OpenReview venue names to pull accepted papers from.",
    )
    biorxiv_categories: list[str] = Field(
        default=["bioinformatics", "neuroscience", "genomics"],
        description="bioRxiv subject category slugs.",
    )
    wikipedia_languages: list[str] = Field(
        default=["en"],
        description="Wikipedia language editions to search (ISO codes, e.g. 'en', 'de', 'fr').",
    )
    max_results_per_query: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Upper bound on papers fetched per source per run.",
    )
    days_lookback: int = Field(
        default=7,
        ge=1,
        description="How many calendar days back to look for new papers.",
    )


class RateLimitSettings(BaseModel):
    """Per-source rate-limit configuration."""

    arxiv_delay_seconds: float = Field(default=3.0, ge=0.5)
    biorxiv_delay_seconds: float = Field(default=1.0, ge=0.5)
    semantic_scholar_delay_seconds: float = Field(default=1.0, ge=0.2)
    openreview_delay_seconds: float = Field(default=1.0, ge=0.5)
    pubmed_delay_seconds: float = Field(
        default=0.34,
        ge=0.1,
        description="Delay between PubMed requests. NCBI allows 3/s without key, 10/s with.",
    )
    max_retries: int = Field(default=3, ge=0)
    retry_backoff_seconds: float = Field(default=5.0, ge=1.0)


class DatabaseSettings(BaseModel):
    """Filesystem paths for local storage."""

    sqlite_path: Path = Field(default=Path("data/db/research_station.db"))
    chroma_path: Path = Field(default=Path("data/db/chroma"))


class EmbeddingSettings(BaseModel):
    """Embedding backend — used for ChromaDB semantic similarity."""

    provider: str = Field(
        default="vllm",
        description="One of: vllm | ollama | sentence_transformers | default",
    )
    model: str = Field(
        default="Qwen/Qwen3-Embedding-0.6B",
        description="Embedding model name. vLLM: Qwen/Qwen3-Embedding-0.6B or -8B. "
        "Ollama: nomic-embed-text, mxbai-embed-large.",
    )
    vllm_base_url: str = Field(default="http://localhost:8888/v1")
    ollama_base_url: str = Field(default="http://localhost:11434")


class LLMSettings(BaseModel):
    """LLM backend configuration (used for summarisation & chat)."""

    provider: str = Field(
        default="anthropic",
        description="One of: anthropic | deepseek | gemini | vllm | ollama",
    )
    model_name: str = Field(default="claude-sonnet-4-6")
    vllm_base_url: str = Field(default="http://localhost:8000/v1")
    ollama_base_url: str = Field(default="http://localhost:11434")
    max_tokens: int = Field(default=4096, ge=256)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    top_p: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Nucleus sampling probability (0–1). None = provider default.",
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        description="Top-K sampling. Ollama and vLLM only. None = provider default.",
    )
    repetition_penalty: float | None = Field(
        default=None,
        ge=0.0,
        description="Penalises repeated tokens. Ollama and some vLLM models. None = off.",
    )
    presence_penalty: float | None = Field(
        default=None,
        description="Encourages new topics by penalising tokens already present. vLLM/OpenAI. None = off.",
    )
    enable_thinking: bool = Field(
        default=False,
        description=(
            "Enable extended thinking / chain-of-thought reasoning. "
            "Supported by Ollama (passes options.think=true) and Anthropic "
            "(passes thinking block; forces temperature=1.0). "
            "Ignored by vLLM and OpenAI."
        ),
    )


class OCRSettings(BaseModel):
    """OCR-specific backend configuration, separate from the chat/summarisation LLM.

    If ``provider`` is None (default), OCR falls back to ``llm.provider``.
    Set this to use a dedicated vision/OCR model while keeping a different
    model for summarisation and chat.

    Example .env for DeepSeek-OCR-2 on vLLM + Anthropic for summarisation::

        LLM__PROVIDER=anthropic
        LLM__MODEL_NAME=claude-sonnet-4-6
        OCR__PROVIDER=vllm
        OCR__MODEL_NAME=deepseek-ai/DeepSeek-OCR-2
        OCR__VLLM_BASE_URL=http://localhost:8000/v1

    Example .env for Ollama vision model::

        OCR__PROVIDER=ollama
        OCR__MODEL_NAME=gemma4:26b
    """

    provider: str | None = Field(
        default=None,
        description="OCR provider override. One of: vllm | ollama | None (inherit from llm)",
    )
    model_name: str | None = Field(default=None, description="OCR model name override.")
    vllm_base_url: str = Field(default="http://localhost:8000/v1")
    ollama_base_url: str = Field(default="http://localhost:11434")
    max_tokens: int = Field(
        default=2048,
        ge=256,
        description="Max output tokens per page. Must leave room for image tokens within the model's context window.",
    )
    semaphore_limit: int = Field(
        default=1,
        ge=1,
        description="Max concurrent page requests to vLLM. Keep at 1 for a single GPU with a large model; raise only if you have free VRAM headroom.",
    )
    dpi: int = Field(
        default=200,
        ge=72,
        le=600,
        description="Resolution for PDF page rendering. Higher = better OCR quality but larger images.",
    )
    backend: str = Field(
        default="auto",
        description=(
            "Which OCR backend to use. "
            "'auto' detects from model name: nanonets → NanonetsOCR (vLLM), "
            "deepseek → DeepSeekOCR (vLLM), qwen → QwenVL (Ollama). "
            "Override with 'nanonets', 'deepseek', or 'qwen' if auto-detection fails."
        ),
    )
    repetition_penalty: float | None = Field(
        default=None,
        description="Nanonets-OCR tip: set to 1.0 for complex tables or financial documents.",
    )
    use_ngram_processor: bool = Field(
        default=False,
        description=(
            "DeepSeek-OCR only: set True when the vLLM server was started with "
            "NGramPerReqLogitsProcessor for better table rendering."
        ),
    )
    text_extract: bool = Field(
        default=False,
        description=(
            "When True, use PyMuPDF direct text extraction instead of vision OCR. "
            "Fast and requires no GPU/LLM, but only works for PDFs with embedded text."
        ),
    )


class AgentSettings(BaseModel):
    """Agentic chat loop configuration."""

    max_turns: int = Field(
        default=30,
        ge=1,
        description="Maximum agent turns per chat message (includes sub-agent tool calls).",
    )
    history_max_turns: int = Field(
        default=8,
        ge=1,
        description="Number of prior user/assistant turn-pairs to include in each request. Older turns are dropped.",
    )
    strip_parallel_tool_calls: bool = Field(
        default=True,
        description=(
            "Strip all but the first tool call from each model response before the SDK processes it. "
            "Works around providers (e.g. DeepSeek V4) that ignore parallel_tool_calls=false and "
            "return multiple tool_calls in one turn, which causes a 400 on the follow-up request. "
            "Set to false when using a provider that handles parallel tool calls correctly."
        ),
    )


class Settings(BaseSettings):
    """Root application settings.

    Environment variable mapping:
        SEMANTIC_SCHOLAR_API_KEY=...
        ANTHROPIC_API_KEY=...
        PREFERENCES__DAYS_LOOKBACK=14
        DATABASE__SQLITE_PATH=/custom/path/rs.db
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # API keys
    semantic_scholar_api_key: str | None = None
    anthropic_api_key: str | None = None
    deepseek_api_key: str | None = None
    gemini_api_key: str | None = None
    openreview_username: str | None = None
    openreview_password: str | None = None
    pubmed_api_key: str | None = None  # optional; triples NCBI rate limit to 10 req/s

    # Top-level paths
    data_dir: Path = Path("data")
    papers_dir: Path = Path("data/papers")
    ocr_dir: Path = Path("data/ocr")
    logs_dir: Path = Path("data/logs")
    web_screenshots_dir: Path = Path("data/web_screenshots")

    # Nested config groups
    preferences: ResearchPreferences = Field(default_factory=ResearchPreferences)
    rate_limits: RateLimitSettings = Field(default_factory=RateLimitSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    ocr: OCRSettings = Field(default_factory=OCRSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)

    def ensure_directories(self) -> None:
        """Create all required data directories if they don't exist."""
        for path in [
            self.data_dir,
            self.papers_dir,
            self.ocr_dir,
            self.logs_dir,
            self.web_screenshots_dir,
            self.database.sqlite_path.parent,
            self.database.chroma_path,
        ]:
            path.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-level singleton Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
