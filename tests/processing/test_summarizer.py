"""Tests for PaperSummarizer and LLM client utilities.

Unit tests mock the LLM client; integration tests require a live provider.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from research_station.models.paper import Author, Paper, PaperSource
from research_station.models.summary import PaperSummary
from research_station.processing.llm.base import LLMResponse, Message
from research_station.processing.llm.ollama import _extract_thinking
from research_station.processing.summarizer import (
    PaperSummarizer,
    _build_prompt,
    _parse_json_response,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_paper() -> Paper:
    return Paper(
        id="arxiv:2301.00001",
        title="Attention Is All You Need (Reprise)",
        abstract="We propose a novel transformer variant that improves upon the original.",
        authors=[Author(name="Alice Smith"), Author(name="Bob Jones")],
        categories=["cs.LG", "cs.CL"],
        source=PaperSource.ARXIV,
        arxiv_id="2301.00001",
        published_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
        updated_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
    )


_VALID_SUMMARY_JSON = {
    "tldr": "A new transformer variant that improves efficiency.",
    "contributions": [
        "Reduces attention complexity to O(n log n)",
        "New positional encoding scheme",
    ],
    "methodology": "Sparse attention with learned routing gates.",
    "key_results": ["BLEU +2.1 over baseline on WMT14"],
    "limitations": ["Only evaluated on NLP tasks"],
    "related_work_context": "Builds on the original Transformer and Longformer architectures.",
    "interesting_aspects": ["Simple to implement", "Hardware-friendly"],
    "suggested_follow_up": ["Linformer", "Flash Attention"],
}


def _make_mock_client(content: str = "", thinking: str = "") -> MagicMock:
    client = MagicMock()
    client.provider_name = "mock"
    client._model = "mock-model"
    client.chat = AsyncMock(
        return_value=LLMResponse(
            content=content,
            thinking=thinking,
            model="mock-model",
            provider="mock",
            generation_time_seconds=0.5,
            prompt_tokens=100,
            completion_tokens=200,
        )
    )
    return client


# ── Unit: _extract_thinking ───────────────────────────────────────────────────


class TestExtractThinking:
    def test_no_tags_returns_empty_thinking(self) -> None:
        thinking, response = _extract_thinking("Hello world")
        assert thinking == ""
        assert response == "Hello world"

    def test_think_tags_extracted(self) -> None:
        raw = "<think>I am reasoning</think>Final answer"
        thinking, response = _extract_thinking(raw)
        assert thinking == "I am reasoning"
        assert response == "Final answer"

    def test_thought_tags_extracted(self) -> None:
        raw = "<thought>Deep reasoning here</thought>Result"
        thinking, response = _extract_thinking(raw)
        assert thinking == "Deep reasoning here"
        assert response == "Result"

    def test_multiline_thinking(self) -> None:
        raw = "<think>\nStep 1\nStep 2\n</think>\nConclusion"
        thinking, response = _extract_thinking(raw)
        assert "Step 1" in thinking
        assert "Conclusion" in response


# ── Unit: prompt builders ─────────────────────────────────────────────────────


class TestBuildPrompt:
    def test_contains_title(self, sample_paper: Paper) -> None:
        prompt = _build_prompt(sample_paper)
        assert sample_paper.title in prompt

    def test_contains_abstract(self, sample_paper: Paper) -> None:
        prompt = _build_prompt(sample_paper)
        assert "novel transformer variant" in prompt

    def test_contains_schema_keys(self, sample_paper: Paper) -> None:
        prompt = _build_prompt(sample_paper)
        for key in ("tldr", "contributions", "methodology", "limitations"):
            assert key in prompt


# ── Unit: JSON parsing ────────────────────────────────────────────────────────


class TestParseJsonResponse:
    def test_clean_json(self) -> None:
        raw = json.dumps(_VALID_SUMMARY_JSON)
        data = _parse_json_response(raw)
        assert data["tldr"] == _VALID_SUMMARY_JSON["tldr"]

    def test_strips_markdown_fences(self) -> None:
        fenced = f"```json\n{json.dumps(_VALID_SUMMARY_JSON)}\n```"
        data = _parse_json_response(fenced)
        assert "tldr" in data

    def test_invalid_json_raises(self) -> None:
        with pytest.raises((json.JSONDecodeError, ValueError)):
            _parse_json_response("not json at all")


# ── Unit: PaperSummarizer ─────────────────────────────────────────────────────


class TestPaperSummarizer:
    @pytest.mark.asyncio
    async def test_valid_response_produces_summary(self, sample_paper: Paper) -> None:
        client = _make_mock_client(content=json.dumps(_VALID_SUMMARY_JSON))
        summarizer = PaperSummarizer(client)
        summary = await summarizer.summarize(sample_paper)

        assert isinstance(summary, PaperSummary)
        assert summary.paper_id == sample_paper.id
        assert summary.tldr == _VALID_SUMMARY_JSON["tldr"]
        assert len(summary.contributions) == 2
        assert summary.provider == "mock"

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error_summary(self, sample_paper: Paper) -> None:
        client = _make_mock_client(content="This is not JSON at all {broken}")
        summarizer = PaperSummarizer(client)
        summary = await summarizer.summarize(sample_paper)

        assert "failed" in summary.tldr.lower()
        assert summary.paper_id == sample_paper.id

    @pytest.mark.asyncio
    async def test_thinking_trace_preserved(self, sample_paper: Paper) -> None:
        client = _make_mock_client(
            content=json.dumps(_VALID_SUMMARY_JSON),
            thinking="Step 1: Read abstract. Step 2: Extract contributions.",
        )
        summarizer = PaperSummarizer(client, enable_thinking=True)
        summary = await summarizer.summarize(sample_paper)

        assert "Step 1" in summary.thinking_trace

    @pytest.mark.asyncio
    async def test_generation_metadata_captured(self, sample_paper: Paper) -> None:
        client = _make_mock_client(content=json.dumps(_VALID_SUMMARY_JSON))
        summarizer = PaperSummarizer(client)
        summary = await summarizer.summarize(sample_paper)

        assert summary.generation_time_seconds == 0.5
        assert summary.prompt_tokens == 100
        assert summary.completion_tokens == 200


# ── Integration tests ─────────────────────────────────────────────────────────


@pytest.mark.integration
class TestOllamaClientIntegration:
    """Requires a running Ollama server at localhost:11434."""

    def test_basic_chat(self) -> None:
        from research_station.processing.llm.ollama import OllamaClient

        client = OllamaClient(model="gemma4:12b", temperature=0.1, max_tokens=256)
        response = client.chat_sync(
            [Message(role="user", content="Reply with: OK")],
            system_prompt="You are a test assistant. Reply with exactly 'OK'.",
        )
        assert response.content
        assert response.provider == "ollama"

    def test_summarize_paper_via_ollama(self, sample_paper: Paper) -> None:
        from research_station.processing.llm.ollama import OllamaClient

        client = OllamaClient(model="gemma4:12b", temperature=0.1, max_tokens=1024)
        summarizer = PaperSummarizer(client)
        summary = summarizer.summarize_sync(sample_paper)

        assert summary.tldr
        assert len(summary.contributions) > 0
        assert summary.provider == "ollama"
