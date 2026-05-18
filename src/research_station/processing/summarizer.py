"""Paper summariser: feeds a paper into an LLM and returns structured analysis.

The LLM is prompted to produce a strict JSON object.  The JSON is parsed into
a ``PaperSummary`` Pydantic model, which is then persisted to the
``paper_summaries`` table.

Prompt strategy
---------------
Prompts live in ``src/research_station/prompts/``:
  - ``summarizer_system.md`` — system persona + output constraint
  - ``summarizer_user.md``   — paper metadata + content + JSON schema template

Input material hierarchy (richest available wins):
  1. Full paper text extracted from local PDF via PDFOCRPipeline
  2. Abstract + title + authors + categories (fallback)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from ..models.paper import Paper
from ..models.summary import PaperSummary
from .llm.base import BaseLLMClient, LLMResponse, Message
from .prompts import load as load_prompt

logger = logging.getLogger(__name__)

_CHUNK_CHARS = 8_000  # chars per map-phase chunk
_MAP_SYSTEM = (
    "You are a research assistant reading one section of a technical paper. "
    "Extract key technical points: methods, results, claims, and findings. "
    "Be concise and precise. Use bullet points."
)

_SUMMARY_SCHEMA = {
    "tldr": "string — 1-2 sentence plain-language summary",
    "contributions": ["string — one main contribution per item"],
    "methodology": "string — how the approach works (model, algorithm, key design choices)",
    "key_results": ["string — one concrete quantitative or qualitative result per item"],
    "limitations": ["string — weaknesses or caveats (explicit or inferred)"],
    "related_work_context": "string — how this fits in the broader landscape (2-3 sentences)",
    "interesting_aspects": ["string — what makes this worth reading"],
    "suggested_follow_up": ["string — related paper topics or research directions to explore next"],
}


def _build_prompt(paper: Paper, full_text: str | None = None) -> str:
    author_names = ", ".join(a.name for a in paper.authors[:6])
    if len(paper.authors) > 6:
        author_names += f" et al. (+{len(paper.authors) - 6})"

    categories = ", ".join(paper.categories[:5]) if paper.categories else "N/A"
    venue = paper.venue or paper.source.value
    tldr_hint = f"\nTLDR (Semantic Scholar auto): {paper.tldr}" if paper.tldr else ""

    if full_text:
        content_block = f"Full paper text (condensed from all sections):\n{full_text}"
        content_label = "FULL TEXT"
        full_text_note = " — use the full text to give richer analysis."
    else:
        abstract = paper.abstract or "(no abstract available)"
        content_block = f"Abstract:\n{abstract}"
        content_label = "ABSTRACT ONLY"
        full_text_note = ""

    return load_prompt("summarizer_user").substitute(
        content_label=content_label,
        full_text_note=full_text_note,
        title=paper.title,
        author_names=author_names,
        venue=venue,
        categories=categories,
        published=paper.published_date.strftime("%Y-%m-%d"),
        tldr_hint=tldr_hint,
        content_block=content_block,
        schema_json=json.dumps(_SUMMARY_SCHEMA, indent=2),
    )


def _parse_json_response(content: str) -> dict[str, object]:
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", content).strip()
    return json.loads(cleaned)  # type: ignore[no-any-return]


def _safe_str(val: object, fallback: str = "") -> str:
    return str(val) if val is not None else fallback


def _safe_list(val: object) -> list[str]:
    if isinstance(val, list):
        return [str(item) for item in val]
    return []


class PaperSummarizer:
    """Generates structured LLM summaries for Paper objects."""

    def __init__(
        self,
        llm_client: BaseLLMClient,
        enable_thinking: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self._client = llm_client
        self._enable_thinking = enable_thinking
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def _map_reduce(self, full_text: str, on_chunk=None) -> str:
        """Split text into chunks, summarise each, return joined chunk summaries."""
        chunks = [full_text[i : i + _CHUNK_CHARS] for i in range(0, len(full_text), _CHUNK_CHARS)]
        logger.info("Map-reduce: %d chunks of ~%d chars each", len(chunks), _CHUNK_CHARS)
        chunk_summaries: list[str] = []
        for idx, chunk in enumerate(chunks, 1):
            user_msg = (
                f"Section {idx}/{len(chunks)} of the paper:\n\n{chunk}\n\n"
                "List the key technical points, methods, results, and claims from this section."
            )
            resp = await self._client.chat(
                [Message(role="user", content=user_msg)],
                system_prompt=_MAP_SYSTEM,
                temperature=0.1,
            )
            chunk_summaries.append(f"[Section {idx}/{len(chunks)}]\n{resp.content.strip()}")
            logger.debug("Chunk %d/%d summarised (%d chars)", idx, len(chunks), len(resp.content))
            if on_chunk:
                on_chunk(idx, len(chunks))
        return "\n\n".join(chunk_summaries)

    async def summarize(
        self, paper: Paper, full_text: str | None = None, on_chunk=None
    ) -> PaperSummary:
        """Generate a structured summary for *paper*.

        Args:
            paper:     The paper to summarise.
            full_text: Optional full extracted text (e.g. from OCR). When
                       provided, the prompt uses the full text instead of just
                       the abstract, producing significantly richer output.
                       Long texts (>12 k chars) are condensed via map-reduce
                       before the final structured synthesis pass.
        """
        condensed: str | None = None
        if full_text:
            logger.info("Summarising %s with full text (%d chars)", paper.id, len(full_text))
            if len(full_text) > 12_000:
                logger.info("Text too long — running map-reduce first")
                condensed = await self._map_reduce(full_text, on_chunk=on_chunk)
                logger.info(
                    "Map-reduce complete: %d chars condensed to %d", len(full_text), len(condensed)
                )
            else:
                condensed = full_text

        system_prompt = load_prompt("summarizer_system").template
        prompt = _build_prompt(paper, full_text=condensed)
        messages = [Message(role="user", content=prompt)]

        logger.info(
            "Summarising %s with %s/%s (thinking=%s)",
            paper.id,
            self._client.provider_name,
            getattr(self._client, "_model", "?"),
            self._enable_thinking,
        )

        llm_response = await self._client.chat(
            messages,
            system_prompt=system_prompt,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            enable_thinking=self._enable_thinking,
        )

        return self._build_summary(paper, prompt, llm_response)

    def summarize_sync(self, paper: Paper, full_text: str | None = None) -> PaperSummary:
        """Blocking wrapper around ``summarize`` for CLI use."""
        import asyncio

        return asyncio.run(self.summarize(paper, full_text=full_text))

    # ── Private ───────────────────────────────────────────────────────────────

    def _build_summary(self, paper: Paper, prompt: str, llm_response: LLMResponse) -> PaperSummary:
        model_label = getattr(self._client, "_model", self._client.provider_name)

        try:
            data = _parse_json_response(llm_response.content)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Failed to parse LLM JSON for %s: %s", paper.id, exc)
            logger.debug("Raw LLM output: %s", llm_response.content[:500])
            return PaperSummary(
                paper_id=paper.id,
                model_used=model_label,
                provider=self._client.provider_name,
                tldr=f"[Summarisation failed: {exc}]",
                contributions=[],
                methodology="",
                key_results=[],
                limitations=[],
                related_work_context="",
                interesting_aspects=[],
                suggested_follow_up=[],
                thinking_trace=llm_response.thinking,
                prompt_used=prompt,
                generated_at=datetime.utcnow(),
                generation_time_seconds=llm_response.generation_time_seconds,
                prompt_tokens=llm_response.prompt_tokens,
                completion_tokens=llm_response.completion_tokens,
            )

        return PaperSummary(
            paper_id=paper.id,
            model_used=model_label,
            provider=self._client.provider_name,
            tldr=_safe_str(data.get("tldr"), fallback="N/A"),
            contributions=_safe_list(data.get("contributions")),
            methodology=_safe_str(data.get("methodology")),
            key_results=_safe_list(data.get("key_results")),
            limitations=_safe_list(data.get("limitations")),
            related_work_context=_safe_str(data.get("related_work_context")),
            interesting_aspects=_safe_list(data.get("interesting_aspects")),
            suggested_follow_up=_safe_list(data.get("suggested_follow_up")),
            thinking_trace=llm_response.thinking,
            prompt_used=prompt,
            generated_at=datetime.utcnow(),
            generation_time_seconds=llm_response.generation_time_seconds,
            prompt_tokens=llm_response.prompt_tokens,
            completion_tokens=llm_response.completion_tokens,
        )
