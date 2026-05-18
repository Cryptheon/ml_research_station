"""OpenAI-compatible chat client.

Handles two providers with a single implementation:

``provider="openai"``
    Targets ``https://api.openai.com/v1`` using a real API key.

``provider="vllm"``
    Targets a local vLLM server (default ``http://localhost:8000/v1``).
    vLLM exposes the exact same REST schema as the OpenAI API; the only
    differences are the base URL and that the API key can be any non-empty
    string ("EMPTY" by convention).

Uses ``httpx`` directly to avoid an ``openai`` package dependency for the
vLLM path, though the openai SDK would work identically if installed.
"""

from __future__ import annotations

import logging
import time

import httpx

from .base import BaseLLMClient, LLMResponse, Message

logger = logging.getLogger(__name__)


class OpenAICompatClient(BaseLLMClient):
    """Client for any OpenAI-schema /v1/chat/completions endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.1,
        provider_name: str = "openai",
        top_p: float | None = None,
        top_k: int | None = None,
        presence_penalty: float | None = None,
    ) -> None:
        self.provider_name = provider_name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._default_max_tokens = max_tokens
        self._default_temperature = temperature
        self._top_p = top_p
        self._top_k = top_k
        self._presence_penalty = presence_penalty

    async def chat(
        self,
        messages: list[Message],
        system_prompt: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        enable_thinking: bool = False,
    ) -> LLMResponse:
        """Send a request to the /v1/chat/completions endpoint.

        Note: ``enable_thinking`` is a no-op for this provider — OpenAI and
        vLLM do not expose a standardised thinking API at this time.
        """
        if enable_thinking:
            logger.debug("enable_thinking=True is ignored for provider=%r", self.provider_name)

        wire_messages: list[dict] = []
        if system_prompt:
            wire_messages.append({"role": "system", "content": system_prompt})
        for m in messages:
            if m.images:
                parts: list[dict] = [{"type": "text", "text": m.content}]
                for img in m.images:
                    parts.append({"type": "image_url", "image_url": {"url": img}})
                wire_messages.append({"role": m.role, "content": parts})
            else:
                wire_messages.append({"role": m.role, "content": m.content})

        payload: dict[str, object] = {
            "model": self._model,
            "messages": wire_messages,
            "max_tokens": max_tokens or self._default_max_tokens,
            "temperature": temperature if temperature is not None else self._default_temperature,
            "stream": False,
        }
        if self._top_p is not None:
            payload["top_p"] = self._top_p
        if self._top_k is not None:
            payload["top_k"] = self._top_k
        if self._presence_penalty is not None:
            payload["presence_penalty"] = self._presence_penalty

        t0 = time.monotonic()
        async with httpx.AsyncClient(
            timeout=180.0,
            headers={"Authorization": f"Bearer {self._api_key}"},
        ) as client:
            response = await client.post(f"{self._base_url}/chat/completions", json=payload)
            response.raise_for_status()
            data: dict[str, object] = response.json()
        elapsed = time.monotonic() - t0

        choices: list[dict[str, object]] = data["choices"]  # type: ignore[index]
        content = str(choices[0]["message"]["content"]).strip()  # type: ignore[index]

        usage: dict[str, int] = data.get("usage") or {}  # type: ignore[assignment]
        llm_response = LLMResponse(
            content=content,
            model=self._model,
            provider=self.provider_name,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            generation_time_seconds=elapsed,
        )
        from .usage import record as _record_usage

        _record_usage(llm_response)
        return llm_response
