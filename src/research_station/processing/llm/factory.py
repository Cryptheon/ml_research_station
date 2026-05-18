"""LLM client factory.

Reads the ``LLMSettings`` + API keys from ``Settings`` and returns the
appropriate ``BaseLLMClient`` subclass.  All provider selection logic lives
here so the rest of the codebase never imports a provider class directly.
"""

from __future__ import annotations

import logging

from ...config.settings import Settings
from .anthropic_client import AnthropicClient
from .base import BaseLLMClient
from .ollama import OllamaClient
from .openai_compat import OpenAICompatClient

logger = logging.getLogger(__name__)

_VLLM_PLACEHOLDER_KEY = "EMPTY"


def create_llm_client(settings: Settings) -> BaseLLMClient:
    """Instantiate the LLM client specified by ``settings.llm.provider``.

    Provider mapping:
        ``anthropic`` → AnthropicClient (requires ANTHROPIC_API_KEY)
        ``deepseek``  → OpenAICompatClient → api.deepseek.com (requires DEEPSEEK_API_KEY)
        ``gemini``    → OpenAICompatClient → generativelanguage.googleapis.com (requires GEMINI_API_KEY)
        ``vllm``      → OpenAICompatClient → localhost:8000/v1 (no real key needed)
        ``ollama``    → OllamaClient       → localhost:11434

    Raises:
        ValueError: If an unknown provider string is supplied.
        RuntimeError: If a required API key is missing.
    """
    llm = settings.llm
    provider = llm.provider.lower()

    if provider == "anthropic":
        return _build_anthropic(settings)
    if provider == "deepseek":
        return _build_deepseek(settings)
    if provider == "gemini":
        return _build_gemini(settings)
    if provider == "vllm":
        return _build_vllm(settings)
    if provider == "ollama":
        return _build_ollama(settings)

    raise ValueError(
        f"Unknown LLM provider: {provider!r}. "
        "Choose one of: anthropic | deepseek | gemini | vllm | ollama"
    )


# ── Private builders ──────────────────────────────────────────────────────────


def _build_anthropic(settings: Settings) -> AnthropicClient:
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is required for provider='anthropic'. "
            "Add it to .env or set the environment variable."
        )
    llm = settings.llm
    logger.info("LLM: Anthropic / %s", llm.model_name)
    return AnthropicClient(
        api_key=settings.anthropic_api_key,
        model=llm.model_name,
        max_tokens=llm.max_tokens,
        temperature=llm.temperature,
        enable_thinking=llm.enable_thinking,
    )


def _build_deepseek(settings: Settings) -> OpenAICompatClient:
    if not settings.deepseek_api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is required for provider='deepseek'. "
            "Add it to .env or set the environment variable."
        )
    llm = settings.llm
    logger.info("LLM: DeepSeek / %s", llm.model_name)
    return OpenAICompatClient(
        base_url="https://api.deepseek.com/v1",
        api_key=settings.deepseek_api_key,
        model=llm.model_name,
        max_tokens=llm.max_tokens,
        temperature=llm.temperature,
        provider_name="deepseek",
        top_p=llm.top_p,
        presence_penalty=llm.presence_penalty,
    )


def _build_gemini(settings: Settings) -> OpenAICompatClient:
    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is required for provider='gemini'. "
            "Add it to .env or set the environment variable."
        )
    llm = settings.llm
    logger.info("LLM: Gemini / %s", llm.model_name)
    return OpenAICompatClient(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key=settings.gemini_api_key,
        model=llm.model_name,
        max_tokens=llm.max_tokens,
        temperature=llm.temperature,
        provider_name="gemini",
        top_p=llm.top_p,
    )


def _build_vllm(settings: Settings) -> OpenAICompatClient:
    llm = settings.llm
    logger.info("LLM: vLLM @ %s / %s", llm.vllm_base_url, llm.model_name)
    return OpenAICompatClient(
        base_url=llm.vllm_base_url,
        api_key=_VLLM_PLACEHOLDER_KEY,
        model=llm.model_name,
        max_tokens=llm.max_tokens,
        temperature=llm.temperature,
        provider_name="vllm",
        top_p=llm.top_p,
        top_k=llm.top_k,
        presence_penalty=llm.presence_penalty,
    )


def _build_ollama(settings: Settings) -> OllamaClient:
    llm = settings.llm
    logger.info("LLM: Ollama @ %s / %s", llm.ollama_base_url, llm.model_name)
    return OllamaClient(
        base_url=llm.ollama_base_url,
        model=llm.model_name,
        max_tokens=llm.max_tokens,
        temperature=llm.temperature,
        top_p=llm.top_p,
        top_k=llm.top_k,
        repetition_penalty=llm.repetition_penalty,
        think=llm.enable_thinking,
    )
