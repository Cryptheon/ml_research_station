"""LLM client protocol: shared data structures and abstract base class.

All four provider implementations (Ollama, vLLM, OpenAI, Anthropic) satisfy
this interface so the rest of the codebase is provider-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Message:
    """Single turn in a conversation."""

    role: str  # "user" | "assistant" | "system"
    content: str
    images: list[str] = field(
        default_factory=list
    )  # base64 data URIs: "data:image/jpeg;base64,..."


@dataclass
class LLMResponse:
    """Normalised response from any provider.

    ``thinking`` captures chain-of-thought output when the model exposes it —
    either via a native API field (Anthropic extended thinking, Ollama thinking
    field) or by parsing ``<think>`` / ``<thought>`` tags from the content.
    This is the raw reasoning trace exposed to the dashboard UI.
    """

    content: str
    thinking: str = ""
    model: str = ""
    provider: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    generation_time_seconds: float = 0.0


class BaseLLMClient(ABC):
    """Contract every provider client must implement.

    All methods are async-first.  Use ``chat_sync`` when you need a blocking
    call (e.g. from a CLI command or a non-async test).
    """

    provider_name: str = ""

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        system_prompt: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        enable_thinking: bool = False,
    ) -> LLMResponse:
        """Send a chat request and return a normalised LLMResponse."""
        ...

    def chat_sync(
        self,
        messages: list[Message],
        system_prompt: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        enable_thinking: bool = False,
    ) -> LLMResponse:
        """Blocking wrapper around ``chat`` for use in synchronous contexts."""
        import asyncio

        return asyncio.run(
            self.chat(
                messages,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                enable_thinking=enable_thinking,
            )
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(provider={self.provider_name!r})"
