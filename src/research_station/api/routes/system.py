"""System endpoints: health, taxonomy, sources, config."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import set_key
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...config import settings as settings_module
from ...config.settings import get_settings
from ...database.repository import CitationRepository, PaperRepository, SummaryRepository
from ...models.taxonomy import TOPICS
from ...processing.embedding_service import reset_embedding_service
from ..deps import get_db
from ..schemas import ConfigOut, HealthOut, IngestPrefs, ServiceKey, SourceOut, TaxonomyLane

router = APIRouter(tags=["system"])

_SOURCES = [
    SourceOut(id="arxiv", display_name="arXiv"),
    SourceOut(id="biorxiv", display_name="bioRxiv"),
    SourceOut(id="openreview", display_name="OpenReview"),
    SourceOut(id="semantic_scholar", display_name="Semantic Scholar"),
    SourceOut(
        id="nature", display_name="Nature", locked=True, lock_reason="Paywalled — metadata only"
    ),
]


@router.get("/system/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)) -> HealthOut:
    settings = get_settings()
    paper_repo = PaperRepository(db)
    citation_repo = CitationRepository(db)
    summary_repo = SummaryRepository(db)

    db_path = Path(settings.database.sqlite_path)
    db_size_mb = db_path.stat().st_size / 1_048_576 if db_path.exists() else 0.0

    return HealthOut(
        paper_count=paper_repo.count(),
        citation_count=citation_repo.count(),
        summary_count=summary_repo.count(),
        db_size_mb=round(db_size_mb, 2),
        llm_provider=settings.llm.provider,
        llm_model=settings.llm.model_name,
    )


@router.get("/system/config", response_model=ConfigOut)
def system_config() -> ConfigOut:
    settings = get_settings()
    env_file = str(Path(os.getcwd()) / ".env")

    keys = [
        ServiceKey(
            name="Semantic Scholar",
            env_var="SEMANTIC_SCHOLAR_API_KEY",
            present=bool(settings.semantic_scholar_api_key),
            optional=True,
            hint="Free tier: 1 req/s. With key: 10 req/s. Register at semanticscholar.org/product/api",
        ),
        ServiceKey(
            name="OpenReview username",
            env_var="OPENREVIEW_USERNAME",
            present=bool(settings.openreview_username),
            optional=True,
            hint="Required to fetch ICLR, NeurIPS, ICML papers. Register free at openreview.net",
        ),
        ServiceKey(
            name="OpenReview password",
            env_var="OPENREVIEW_PASSWORD",
            present=bool(settings.openreview_password),
            optional=True,
            hint="Your openreview.net account password",
        ),
        ServiceKey(
            name="Anthropic",
            env_var="ANTHROPIC_API_KEY",
            present=bool(settings.anthropic_api_key),
            optional=settings.llm.provider != "anthropic",
            hint="Required when LLM__PROVIDER=anthropic",
        ),
        ServiceKey(
            name="DeepSeek",
            env_var="DEEPSEEK_API_KEY",
            present=bool(settings.deepseek_api_key),
            optional=settings.llm.provider != "deepseek",
            hint="Required when LLM__PROVIDER=deepseek",
        ),
        ServiceKey(
            name="Gemini",
            env_var="GEMINI_API_KEY",
            present=bool(settings.gemini_api_key),
            optional=settings.llm.provider != "gemini",
            hint="Required when LLM__PROVIDER=gemini",
        ),
    ]

    base_url = None
    if settings.llm.provider == "vllm":
        base_url = settings.llm.vllm_base_url
    elif settings.llm.provider == "ollama":
        base_url = settings.llm.ollama_base_url

    ocr = settings.ocr
    ocr_provider = ocr.provider
    ocr_model = ocr.model_name
    ocr_base_url = None
    if ocr_provider == "vllm":
        ocr_base_url = ocr.vllm_base_url
    elif ocr_provider == "ollama":
        ocr_base_url = ocr.ollama_base_url

    return ConfigOut(
        keys=keys,
        embed_provider=settings.embedding.provider,
        embed_model=settings.embedding.model,
        embed_vllm_base_url=settings.embedding.vllm_base_url,
        embed_ollama_base_url=settings.embedding.ollama_base_url,
        llm_provider=settings.llm.provider,
        llm_model=settings.llm.model_name,
        llm_base_url=base_url,
        llm_temperature=settings.llm.temperature,
        llm_max_tokens=settings.llm.max_tokens,
        llm_top_p=settings.llm.top_p,
        llm_top_k=settings.llm.top_k,
        llm_repetition_penalty=settings.llm.repetition_penalty,
        llm_presence_penalty=settings.llm.presence_penalty,
        llm_enable_thinking=settings.llm.enable_thinking,
        ocr_provider=ocr_provider,
        ocr_model=ocr_model,
        ocr_base_url=ocr_base_url,
        ocr_max_tokens=ocr.max_tokens,
        ocr_dpi=ocr.dpi,
        ocr_semaphore_limit=ocr.semaphore_limit,
        ocr_backend=ocr.backend,
        ocr_use_ngram_processor=ocr.use_ngram_processor,
        ocr_repetition_penalty=ocr.repetition_penalty,
        ocr_text_extract=ocr.text_extract,
        env_file=env_file,
        prefs=IngestPrefs(
            max_results_per_source=settings.preferences.max_results_per_query,
            days_lookback=settings.preferences.days_lookback,
            arxiv_categories=settings.preferences.arxiv_categories,
            biorxiv_categories=settings.preferences.biorxiv_categories,
            wikipedia_languages=settings.preferences.wikipedia_languages,
        ),
        agent_strip_parallel_tool_calls=settings.agent.strip_parallel_tool_calls,
    )


class ConfigIn(BaseModel):
    semantic_scholar_api_key: str | None = None
    openreview_username: str | None = None
    openreview_password: str | None = None
    anthropic_api_key: str | None = None
    deepseek_api_key: str | None = None
    gemini_api_key: str | None = None
    embed_provider: str | None = None
    embed_model: str | None = None
    embed_vllm_base_url: str | None = None
    embed_ollama_base_url: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_temperature: float | None = None
    llm_max_tokens: int | None = None
    llm_top_p: float | None = None
    llm_top_k: int | None = None
    llm_repetition_penalty: float | None = None
    llm_presence_penalty: float | None = None
    llm_enable_thinking: bool | None = None
    ocr_provider: str | None = None
    ocr_model: str | None = None
    ocr_base_url: str | None = None
    ocr_max_tokens: int | None = None
    ocr_dpi: int | None = None
    ocr_semaphore_limit: int | None = None
    ocr_backend: str | None = None
    ocr_use_ngram_processor: bool | None = None
    ocr_repetition_penalty: float | None = None
    ocr_text_extract: bool | None = None
    max_results_per_source: int | None = None
    days_lookback: int | None = None
    arxiv_categories: list[str] | None = None
    biorxiv_categories: list[str] | None = None
    wikipedia_languages: list[str] | None = None
    agent_strip_parallel_tool_calls: bool | None = None


_ENV_FILE = Path(os.getcwd()) / ".env"

_KEY_MAP = {
    "semantic_scholar_api_key": "SEMANTIC_SCHOLAR_API_KEY",
    "openreview_username": "OPENREVIEW_USERNAME",
    "openreview_password": "OPENREVIEW_PASSWORD",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
    "llm_provider": "LLM__PROVIDER",
    "llm_model": "LLM__MODEL_NAME",
    "llm_base_url": None,  # provider-dependent, handled specially
    "llm_temperature": "LLM__TEMPERATURE",
    "llm_max_tokens": "LLM__MAX_TOKENS",
    "llm_top_p": "LLM__TOP_P",
    "llm_top_k": "LLM__TOP_K",
    "llm_repetition_penalty": "LLM__REPETITION_PENALTY",
    "llm_presence_penalty": "LLM__PRESENCE_PENALTY",
    "llm_enable_thinking": "LLM__ENABLE_THINKING",
    "ocr_provider": "OCR__PROVIDER",
    "ocr_model": "OCR__MODEL_NAME",
    "ocr_base_url": None,  # provider-dependent, handled specially
    "ocr_max_tokens": "OCR__MAX_TOKENS",
    "ocr_dpi": "OCR__DPI",
    "ocr_semaphore_limit": "OCR__SEMAPHORE_LIMIT",
    "ocr_backend": "OCR__BACKEND",
    "ocr_use_ngram_processor": "OCR__USE_NGRAM_PROCESSOR",
    "ocr_repetition_penalty": "OCR__REPETITION_PENALTY",
    "ocr_text_extract": "OCR__TEXT_EXTRACT",
    "max_results_per_source": None,  # handled specially (needs JSON encoding)
    "days_lookback": "PREFERENCES__DAYS_LOOKBACK",
    "arxiv_categories": None,  # needs JSON encoding
    "agent_strip_parallel_tool_calls": "AGENT__STRIP_PARALLEL_TOOL_CALLS",
}


@router.put("/system/config", response_model=ConfigOut)
def update_config(body: ConfigIn) -> ConfigOut:
    # Ensure .env exists (creates empty file if missing)
    _ENV_FILE.touch(exist_ok=True)

    updates: dict[str, str] = {}
    for field, env_var in _KEY_MAP.items():
        value = getattr(body, field, None)
        if value is None or env_var is None:
            continue
        updates[env_var] = str(value)

    # llm_base_url maps to provider-specific var
    if body.llm_base_url is not None:
        provider = body.llm_provider or get_settings().llm.provider
        if provider == "ollama":
            updates["LLM__OLLAMA_BASE_URL"] = body.llm_base_url
        elif provider == "vllm":
            updates["LLM__VLLM_BASE_URL"] = body.llm_base_url

    # ocr_base_url maps to OCR provider-specific var
    if body.ocr_base_url is not None:
        ocr_provider = body.ocr_provider or get_settings().ocr.provider or ""
        if ocr_provider == "ollama":
            updates["OCR__OLLAMA_BASE_URL"] = body.ocr_base_url
        elif ocr_provider == "vllm":
            updates["OCR__VLLM_BASE_URL"] = body.ocr_base_url

    # Ingestion preferences
    if body.max_results_per_source is not None:
        updates["PREFERENCES__MAX_RESULTS_PER_QUERY"] = str(body.max_results_per_source)
    if body.days_lookback is not None:
        updates["PREFERENCES__DAYS_LOOKBACK"] = str(body.days_lookback)
    if body.arxiv_categories is not None:
        import json

        updates["PREFERENCES__ARXIV_CATEGORIES"] = json.dumps(body.arxiv_categories)
    if body.biorxiv_categories is not None:
        import json

        updates["PREFERENCES__BIORXIV_CATEGORIES"] = json.dumps(body.biorxiv_categories)
    if body.wikipedia_languages is not None:
        import json

        updates["PREFERENCES__WIKIPEDIA_LANGUAGES"] = json.dumps(body.wikipedia_languages)

    # Embedding settings
    embed_changed = False
    if body.embed_provider is not None:
        updates["EMBEDDING__PROVIDER"] = body.embed_provider
        embed_changed = True
    if body.embed_model is not None:
        updates["EMBEDDING__MODEL"] = body.embed_model
        embed_changed = True
    if body.embed_vllm_base_url is not None:
        updates["EMBEDDING__VLLM_BASE_URL"] = body.embed_vllm_base_url
        embed_changed = True
    if body.embed_ollama_base_url is not None:
        updates["EMBEDDING__OLLAMA_BASE_URL"] = body.embed_ollama_base_url
        embed_changed = True

    for env_var, value in updates.items():
        set_key(str(_ENV_FILE), env_var, str(value), quote_mode="never")

    # Invalidate settings singleton so next call re-reads .env
    settings_module._settings = None
    if embed_changed:
        reset_embedding_service()

    return system_config()


_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


class PromptMeta(BaseModel):
    name: str
    description: str
    used_by: str
    notes: str
    group: str = "core"  # "core" | "skill"
    triggers: str = ""  # comma-separated trigger keywords (skills only)


class PromptOut(PromptMeta):
    raw: str  # full file content including frontmatter


class PromptBodyIn(BaseModel):
    raw: str


class SkillCreateIn(BaseModel):
    name: str  # filename stem, e.g. "my_skill"
    raw: str  # full file content including frontmatter


def _parse_prompt(path: Path, group: str = "core") -> PromptOut:
    text = path.read_text()
    meta: dict[str, str] = {}
    if text.startswith("---"):
        try:
            end = text.index("---", 3)
        except ValueError:
            end = 0
        fm_lines = text[3:end].splitlines()
        current_key: str | None = None
        current_val: list[str] = []
        for line in fm_lines:
            if line and not line.startswith(" ") and ":" in line:
                if current_key:
                    meta[current_key] = " ".join(current_val).strip()
                k, _, v = line.partition(":")
                current_key = k.strip()
                v = v.strip()
                current_val = [] if v in (">", "|") else [v]
            elif current_key and line.startswith(" "):
                current_val.append(line.strip())
        if current_key:
            meta[current_key] = " ".join(current_val).strip()
    # Skills use "skills/<stem>" as their name so the frontend can route save calls correctly
    name = f"skills/{path.stem}" if group == "skill" else path.stem
    return PromptOut(
        name=name,
        description=meta.get("description", ""),
        used_by=meta.get("used_by", ""),
        notes=meta.get("notes", ""),
        group=group,
        triggers=meta.get("triggers", ""),
        raw=text,
    )


@router.get("/prompts", response_model=list[PromptOut])
def list_prompts() -> list[PromptOut]:
    results: list[PromptOut] = []
    if _PROMPTS_DIR.exists():
        results += [_parse_prompt(p, "core") for p in sorted(_PROMPTS_DIR.glob("*.md"))]
    agents_dir = _PROMPTS_DIR / "agents"
    if agents_dir.exists():
        results += [_parse_prompt(p, "agent") for p in sorted(agents_dir.glob("*.md"))]
    skills_dir = _PROMPTS_DIR / "skills"
    if skills_dir.exists():
        results += [_parse_prompt(p, "skill") for p in sorted(skills_dir.glob("*.md"))]
    return results


@router.post("/prompts/skills", response_model=PromptOut, status_code=201)
def create_skill(body: SkillCreateIn) -> PromptOut:
    safe = body.name.strip().lower().replace(" ", "_")
    if not safe or "/" in safe or ".." in safe:
        raise HTTPException(400, "Invalid skill name")
    skills_dir = _PROMPTS_DIR / "skills"
    skills_dir.mkdir(exist_ok=True)
    path = skills_dir / f"{safe}.md"
    if path.exists():
        raise HTTPException(409, f"Skill '{safe}' already exists")
    path.write_text(body.raw, encoding="utf-8")
    return _parse_prompt(path, "skill")


def _prompt_group(name: str) -> str:
    if name.startswith("skills/"):
        return "skill"
    if name.startswith("agents/"):
        return "agent"
    return "core"


@router.get("/prompts/{name:path}", response_model=PromptOut)
def get_prompt(name: str) -> PromptOut:
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise HTTPException(404, f"Prompt '{name}' not found")
    return _parse_prompt(path, _prompt_group(name))


@router.put("/prompts/{name:path}", response_model=PromptOut)
def save_prompt(name: str, body: PromptBodyIn) -> PromptOut:
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise HTTPException(404, f"Prompt '{name}' not found")
    path.write_text(body.raw, encoding="utf-8")
    return _parse_prompt(path, _prompt_group(name))


@router.delete("/prompts/{name:path}", status_code=204)
def delete_prompt(name: str) -> None:
    if not name.startswith("skills/"):
        raise HTTPException(403, "Only skills can be deleted")
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise HTTPException(404, f"Prompt '{name}' not found")
    path.unlink()


@router.get("/workspace/files")
def list_workspace_files(paper_id: str | None = None) -> list[dict]:
    """Return HTML files in the agent workspace directory, newest first.

    If paper_id is provided, only files for that paper's subdirectory are returned.
    """
    workspace = Path(__file__).resolve().parents[4] / "workspace"
    if not workspace.exists():
        return []

    base_url = "http://localhost:8080"

    if paper_id:
        safe = paper_id.replace(":", "_").replace("/", "_").replace(" ", "_")
        sub_dir = workspace / safe
        if not sub_dir.exists():
            return []
        files = sorted(
            [f for f in sub_dir.iterdir() if f.suffix.lower() in (".html", ".htm")],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        return [
            {
                "name": f.name,
                "url": f"{base_url}/workspace/{safe}/{f.name}",
                "size": f.stat().st_size,
                "mtime": f.stat().st_mtime,
            }
            for f in files
        ]

    # No filter — return all files from all paper subdirectories
    all_files: list[tuple[Any, str]] = []
    for sub in workspace.iterdir():
        if sub.is_dir():
            for f in sub.iterdir():
                if f.suffix.lower() in (".html", ".htm"):
                    all_files.append((f, sub.name))
    all_files.sort(key=lambda t: t[0].stat().st_mtime, reverse=True)
    return [
        {
            "name": f.name,
            "url": f"{base_url}/workspace/{sub}/{f.name}",
            "size": f.stat().st_size,
            "mtime": f.stat().st_mtime,
        }
        for f, sub in all_files
    ]


@router.get("/system/token-usage")
def token_usage_stats(db: Session = Depends(get_db)) -> dict:
    from datetime import timedelta

    from sqlalchemy import text as sa_text

    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=6)

    total_rows = db.execute(
        sa_text(
            "SELECT COUNT(*) as requests, SUM(prompt_tokens) as prompt, SUM(completion_tokens) as completion FROM token_usage"
        )
    ).fetchall()

    today_rows = db.execute(
        sa_text(
            "SELECT COUNT(*) as requests, SUM(prompt_tokens) as prompt, SUM(completion_tokens) as completion FROM token_usage WHERE created_at >= :since"
        ),
        {"since": today_start.isoformat()},
    ).fetchall()

    week_rows = db.execute(
        sa_text(
            "SELECT COUNT(*) as requests, SUM(prompt_tokens) as prompt, SUM(completion_tokens) as completion FROM token_usage WHERE created_at >= :since"
        ),
        {"since": week_start.isoformat()},
    ).fetchall()

    by_model = db.execute(
        sa_text(
            "SELECT provider, model, endpoint, COUNT(*) as requests, SUM(prompt_tokens) as prompt_tokens, SUM(completion_tokens) as completion_tokens, AVG(generation_time_seconds) as avg_latency FROM token_usage GROUP BY provider, model, endpoint ORDER BY requests DESC LIMIT 50"
        )
    ).fetchall()

    def row_to_dict(r: object) -> dict:
        return {
            "requests": r.requests,  # type: ignore[union-attr]
            "prompt_tokens": r.prompt or 0,  # type: ignore[union-attr]
            "completion_tokens": r.completion or 0,  # type: ignore[union-attr]
        }

    empty: dict = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
    return {
        "total": row_to_dict(total_rows[0]) if total_rows else empty,
        "today": row_to_dict(today_rows[0]) if today_rows else empty,
        "week": row_to_dict(week_rows[0]) if week_rows else empty,
        "by_model": [
            {
                "provider": r.provider,
                "model": r.model,
                "endpoint": r.endpoint,
                "requests": r.requests,
                "prompt_tokens": r.prompt_tokens or 0,
                "completion_tokens": r.completion_tokens or 0,
                "avg_latency_s": round(r.avg_latency or 0, 2),
            }
            for r in by_model
        ],
    }


@router.post("/system/paper-views")
def record_paper_view(body: dict, db: Session = Depends(get_db)) -> dict:
    from ...models.paper_view import PaperViewORM

    paper_id = body.get("paper_id", "")
    viewer = body.get("viewer", "user")
    if not paper_id:
        raise HTTPException(status_code=400, detail="paper_id required")
    db.add(PaperViewORM(paper_id=paper_id, viewer=viewer))
    db.commit()
    return {"ok": True}


@router.get("/system/paper-views")
def get_paper_views(db: Session = Depends(get_db)) -> dict:
    from sqlalchemy import text as sa_text

    recent = db.execute(
        sa_text(
            """SELECT pv.paper_id, pv.viewer, pv.created_at, p.title
           FROM paper_views pv
           LEFT JOIN papers p ON p.id = pv.paper_id
           ORDER BY pv.created_at DESC LIMIT 100"""
        )
    ).fetchall()

    top_user = db.execute(
        sa_text(
            """SELECT pv.paper_id, p.title, COUNT(*) as views
           FROM paper_views pv LEFT JOIN papers p ON p.id = pv.paper_id
           WHERE pv.viewer='user' GROUP BY pv.paper_id ORDER BY views DESC LIMIT 10"""
        )
    ).fetchall()

    top_agent = db.execute(
        sa_text(
            """SELECT pv.paper_id, p.title, COUNT(*) as views
           FROM paper_views pv LEFT JOIN papers p ON p.id = pv.paper_id
           WHERE pv.viewer='agent' GROUP BY pv.paper_id ORDER BY views DESC LIMIT 10"""
        )
    ).fetchall()

    return {
        "recent": [
            {"paper_id": r.paper_id, "viewer": r.viewer, "at": r.created_at, "title": r.title}
            for r in recent
        ],
        "top_user": [
            {"paper_id": r.paper_id, "title": r.title, "views": r.views} for r in top_user
        ],
        "top_agent": [
            {"paper_id": r.paper_id, "title": r.title, "views": r.views} for r in top_agent
        ],
    }


@router.get("/taxonomy/lanes", response_model=list[TaxonomyLane])
def taxonomy_lanes() -> list[TaxonomyLane]:
    return [TaxonomyLane(id=t.lower().replace(" ", "_"), label=t) for t in TOPICS]


@router.get("/sources", response_model=list[SourceOut])
def sources() -> list[SourceOut]:
    return _SOURCES
