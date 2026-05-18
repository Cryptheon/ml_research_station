"""Anthropic API client with extended thinking support.

Extended thinking (``enable_thinking=True``) uses Anthropic's native
reasoning API, which streams back a separate ``thinking`` content block
before the text response.  The raw thinking text is preserved in
``LLMResponse.thinking`` so the dashboard can render the full reasoning trace.

Constraints imposed by the Anthropic API when thinking is enabled:
  - ``temperature`` must be 1.0 (enforced automatically here).
  - ``budget_tokens`` controls how many tokens the model may use for
    reasoning; defaults to half of ``max_tokens``.
  - Streaming is supported but we use non-streaming for simplicity — the
    dashboard renders the completed trace, not a live stream.
"""

from __future__ import annotations

import logging
import time

from .base import BaseLLMClient, LLMResponse, Message

logger = logging.getLogger(__name__)

_DEFAULT_THINKING_BUDGET_RATIO = 0.5


class AnthropicClient(BaseLLMClient):
    """Async Anthropic Messages API client."""

    provider_name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 4096,
        temperature: float = 0.1,
        enable_thinking: bool = False,
    ) -> None:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise RuntimeError("Install the 'llm' extras: uv pip install -e '.[llm]'") from exc

        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._default_max_tokens = max_tokens
        self._default_temperature = temperature
        self._enable_thinking = enable_thinking

    async def chat(
        self,
        messages: list[Message],
        system_prompt: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        enable_thinking: bool = False,
    ) -> LLMResponse:
        """Send a request to the Anthropic Messages API.

        When ``enable_thinking`` is True the model is given a reasoning budget
        equal to half of ``max_tokens`` and temperature is forced to 1.0 per
        the API contract.
        """
        # Anthropic does not support "system" role in the messages array
        anthropic_messages = []
        for m in messages:
            if m.role == "system":
                continue
            if m.images:
                parts: list[dict] = []
                for img in m.images:
                    header, data = (
                        img.split(",", 1) if "," in img else ("data:image/jpeg;base64", img)
                    )
                    media_type = (
                        header.split(":")[1].split(";")[0] if ":" in header else "image/jpeg"
                    )
                    parts.append(
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": data},
                        }
                    )
                parts.append({"type": "text", "text": m.content})
                anthropic_messages.append({"role": m.role, "content": parts})
            else:
                anthropic_messages.append({"role": m.role, "content": m.content})

        resolved_max_tokens = max_tokens or self._default_max_tokens

        kwargs: dict[str, object] = {
            "model": self._model,
            "max_tokens": resolved_max_tokens,
            "messages": anthropic_messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        if enable_thinking or self._enable_thinking:
            budget = max(256, int(resolved_max_tokens * _DEFAULT_THINKING_BUDGET_RATIO))
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
            kwargs["temperature"] = 1.0  # API requirement
        else:
            kwargs["temperature"] = (
                temperature if temperature is not None else self._default_temperature
            )

        t0 = time.monotonic()
        response = await self._client.messages.create(**kwargs)  # type: ignore[arg-type]
        elapsed = time.monotonic() - t0

        thinking_text = ""
        content_parts: list[str] = []

        for block in response.content:
            block_type = getattr(block, "type", "")
            if block_type == "thinking":
                thinking_text = getattr(block, "thinking", "")
            elif block_type == "text":
                content_parts.append(getattr(block, "text", ""))

        llm_response = LLMResponse(
            content="\n".join(content_parts).strip(),
            thinking=thinking_text,
            model=self._model,
            provider=self.provider_name,
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            generation_time_seconds=elapsed,
        )
        from .usage import record as _record_usage

        _record_usage(llm_response)
        return llm_response
