"""Ollama /api/chat client.

Adapted from the project's existing Ollama integration.  Key behaviours
preserved:
  - Extended thinking via ``think: bool`` (passes ``options.think=true``).
  - Handles *both* native thinking field (gemma4, newer builds) *and*
    ``<think>`` / ``<thought>`` tag parsing (older qwen3 builds).
  - Optional base64 image attachment on the last message turn.
  - Uses ``httpx`` exclusively — no extra dependencies.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncGenerator

import httpx

from .base import BaseLLMClient, LLMResponse, Message

logger = logging.getLogger(__name__)


def _extract_thinking(content: str) -> tuple[str, str]:
    """Split content into (thinking, response) by parsing think/thought tags.

    Returns:
        ``("", content)`` when no thinking block is present.
    """
    pattern = r"<(?:think|thought)>(.*?)</(?:think|thought)>"
    match = re.search(pattern, content, flags=re.DOTALL)
    if match:
        thinking = match.group(1).strip()
        response = re.sub(pattern, "", content, flags=re.DOTALL).strip()
        if not response:
            # Small models (e.g. Qwen3-4b) sometimes put the final answer inside
            # the <think> block and output nothing after </think>.  Fall back to
            # the last non-empty paragraph of the thinking block as the answer.
            paragraphs = [p.strip() for p in thinking.split("\n\n") if p.strip()]
            response = paragraphs[-1] if paragraphs else thinking
        return thinking, response
    return "", content.strip()


def build_messages(
    history: list[Message],
    system_prompt: str = "",
) -> list[dict]:
    """Convert Message list to Ollama wire format, prepending system prompt."""
    result: list[dict] = []
    if system_prompt:
        result.append({"role": "system", "content": system_prompt})
    for m in history:
        entry: dict = {"role": m.role, "content": m.content}
        if m.images:
            # Strip the data URI prefix — Ollama expects raw base64
            raw_images = []
            for img in m.images:
                if "," in img:
                    raw_images.append(img.split(",", 1)[1])
                else:
                    raw_images.append(img)
            entry["images"] = raw_images
        result.append(entry)
    return result


class OllamaClient(BaseLLMClient):
    """Async client for the Ollama /api/chat endpoint."""

    provider_name = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "gemma4:26b",
        max_tokens: int = 4096,
        temperature: float = 0.1,
        think: bool = False,
        top_p: float | None = None,
        top_k: int | None = None,
        repetition_penalty: float | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._default_max_tokens = max_tokens
        self._default_temperature = temperature
        self._think = think
        self._top_p = top_p
        self._top_k = top_k
        self._repetition_penalty = repetition_penalty

    async def chat(
        self,
        messages: list[Message],
        system_prompt: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        enable_thinking: bool = False,
    ) -> LLMResponse:
        wire_messages = build_messages(messages, system_prompt)

        use_think = enable_thinking or self._think
        options: dict[str, object] = {
            "temperature": temperature if temperature is not None else self._default_temperature,
            "num_predict": max_tokens or self._default_max_tokens,
        }
        if self._top_p is not None:
            options["top_p"] = self._top_p
        if self._top_k is not None:
            options["top_k"] = self._top_k
        if self._repetition_penalty is not None:
            options["repeat_penalty"] = self._repetition_penalty

        payload = {
            "model": self._model,
            "messages": wire_messages,
            "stream": False,
            "think": use_think,  # top-level field, not inside options
            "options": options,
        }

        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(f"{self._base_url}/api/chat", json=payload)
            response.raise_for_status()
            data: dict[str, object] = response.json()
        elapsed = time.monotonic() - t0

        msg: dict[str, object] = data["message"]  # type: ignore[index]
        raw_content = str(msg.get("content", ""))

        prompt_tokens = int(data.get("prompt_eval_count") or 0) or None
        completion_tokens = int(data.get("eval_count") or 0) or None

        # Native thinking field (gemma4 and newer Ollama builds)
        native_thinking = str(msg.get("thinking", "")).strip()
        if native_thinking:
            clean_content = re.sub(
                r"<(?:think|thought)>.*?</(?:think|thought)>",
                "",
                raw_content,
                flags=re.DOTALL,
            ).strip()
            llm_response = LLMResponse(
                content=clean_content,
                thinking=native_thinking,
                model=self._model,
                provider=self.provider_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                generation_time_seconds=elapsed,
            )
            from .usage import record as _record_usage

            _record_usage(llm_response)
            return llm_response

        # Fallback: parse <think>/<thought> tags from content
        thinking, content = _extract_thinking(raw_content)
        llm_response = LLMResponse(
            content=content,
            thinking=thinking,
            model=self._model,
            provider=self.provider_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            generation_time_seconds=elapsed,
        )
        from .usage import record as _record_usage

        _record_usage(llm_response)
        return llm_response

    async def stream_chat(
        self,
        messages: list[Message],
        system_prompt: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        enable_thinking: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """Stream chat response as an async generator.

        Yields dicts with shape:
            ``{"type": "thinking", "delta": str}``  — thinking trace chunk
            ``{"type": "content",  "delta": str}``  — response text chunk
        """
        wire_messages = build_messages(messages, system_prompt)
        use_think = enable_thinking or self._think
        options: dict[str, object] = {
            "temperature": temperature if temperature is not None else self._default_temperature,
            "num_predict": max_tokens or self._default_max_tokens,
        }
        if self._top_p is not None:
            options["top_p"] = self._top_p
        if self._top_k is not None:
            options["top_k"] = self._top_k
        if self._repetition_penalty is not None:
            options["repeat_penalty"] = self._repetition_penalty

        payload = {
            "model": self._model,
            "messages": wire_messages,
            "stream": True,
            "think": use_think,
            "options": options,
        }

        # State machine for inline <think> tag parsing across chunk boundaries
        pending = ""
        in_think = False
        _OPEN = re.compile(r"<(?:think|thought)>")
        _CLOSE = re.compile(r"</(?:think|thought)>")

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", f"{self._base_url}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg = data.get("message", {})

                    # Native thinking field (gemma4, newer Ollama with Qwen3)
                    native_thinking = str(msg.get("thinking", ""))
                    if native_thinking:
                        yield {"type": "thinking", "delta": native_thinking}
                        continue

                    content_delta = str(msg.get("content", ""))
                    if not content_delta:
                        continue

                    pending += content_delta

                    # Drain pending buffer, routing to thinking vs content
                    while True:
                        if not in_think:
                            m = _OPEN.search(pending)
                            if m:
                                before = pending[: m.start()]
                                if before:
                                    yield {"type": "content", "delta": before}
                                pending = pending[m.end() :]
                                in_think = True
                            else:
                                safe = max(0, len(pending) - 20)
                                if safe:
                                    yield {"type": "content", "delta": pending[:safe]}
                                    pending = pending[safe:]
                                break
                        else:
                            m = _CLOSE.search(pending)
                            if m:
                                chunk = pending[: m.start()]
                                if chunk:
                                    yield {"type": "thinking", "delta": chunk}
                                pending = pending[m.end() :]
                                in_think = False
                            else:
                                safe = max(0, len(pending) - 20)
                                if safe:
                                    yield {"type": "thinking", "delta": pending[:safe]}
                                    pending = pending[safe:]
                                break

        # Flush any remaining buffered text
        if pending:
            yield {"type": "thinking" if in_think else "content", "delta": pending}
