"""Thread-safe token usage recorder.

Writes one row to the ``token_usage`` table for every LLM call that returns
token counts.  Failures are silently swallowed so a DB hiccup never breaks the
calling code.
"""

from __future__ import annotations

import logging
import threading

from .base import LLMResponse

logger = logging.getLogger(__name__)

_engine_lock = threading.Lock()
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                from ...config.settings import get_settings
                from ...database.engine import build_engine

                _engine = build_engine(get_settings().database.sqlite_path)
    return _engine


def record(response: LLMResponse, endpoint: str = "chat") -> None:
    """Persist token usage for one LLM call.  Silent on failure."""
    if not response.prompt_tokens and not response.completion_tokens:
        return
    try:
        from ...database.engine import build_session_factory, get_session
        from ...models.token_usage import TokenUsageORM

        engine = _get_engine()
        factory = build_session_factory(engine)
        with get_session(factory) as sess:
            sess.add(
                TokenUsageORM(
                    provider=response.provider,
                    model=response.model,
                    endpoint=endpoint,
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    generation_time_seconds=response.generation_time_seconds,
                )
            )
    except Exception as exc:
        logger.debug("Token usage recording failed: %s", exc)
