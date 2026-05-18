"""Agentic chat loop — OpenAI Agents SDK → local vLLM / Ollama.

Uses the OpenAI Agents SDK (openai-agents) with an OpenAI-compatible
provider (vLLM or Ollama).  No Anthropic dependency; no paid API.

SSE events yielded:
  thinking    delta: str          — model reasoning trace (ThinkingPill)
  content     delta: str          — streamed response text
  tool_call   id, tool, input     — tool invoked (with parsed args)
  tool_result id, tool, content   — tool returned
  done                            — loop finished
  error       message: str        — fatal error
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator

import httpx

log = logging.getLogger(__name__)


# ── Parallel-tool-call filter (SSE transport layer) ───────────────────────────


class _FirstToolCallStream(httpx.AsyncByteStream):
    """Re-streams an SSE response, dropping tool_calls with index > 0.

    Some providers (e.g. DeepSeek V4) ignore parallel_tool_calls=false and
    return multiple tool_calls in a single assistant turn.  The SDK then builds
    a follow-up request with N tool_calls but we may only have M<N results,
    causing a 400.  Keeping only index-0 calls forces the model to call one
    tool per turn (it will call the others on subsequent turns).
    """

    def __init__(self, source: httpx.AsyncByteStream) -> None:
        self._source = source

    def _filter_line(self, line: bytes) -> bytes:
        if not line.startswith(b"data: "):
            return line
        payload = line[6:].strip()
        if payload in (b"[DONE]", b""):
            return line
        try:
            data = json.loads(payload)
            modified = False
            for choice in data.get("choices", []):
                delta = choice.get("delta", {})
                tcs = delta.get("tool_calls")
                if not tcs:
                    continue
                # DeepSeek streams parallel tool calls as separate chunks, each
                # containing exactly ONE item with its index. Filtering by
                # len(tcs) > 1 never fires. Filter by index > 0 instead.
                filtered = [tc for tc in tcs if tc.get("index", 0) == 0]
                if len(filtered) != len(tcs):
                    if filtered:
                        delta["tool_calls"] = filtered
                    else:
                        del delta["tool_calls"]
                    modified = True
            # Only re-encode when we actually changed something; return the
            # original bytes otherwise so we never corrupt chunks needlessly.
            return (b"data: " + json.dumps(data).encode()) if modified else line
        except Exception:
            return line

    async def __aiter__(self) -> AsyncIterator[bytes]:
        buf = b""
        async for chunk in self._source:
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                yield self._filter_line(line) + b"\n"
        if buf:
            yield self._filter_line(buf)

    async def aclose(self) -> None:
        await self._source.aclose()


class _SingleToolCallTransport(httpx.AsyncBaseTransport):
    """Wraps a transport and applies _FirstToolCallStream to SSE responses."""

    def __init__(self, wrapped: httpx.AsyncBaseTransport) -> None:
        self._wrapped = wrapped

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._wrapped.handle_async_request(request)
        if "text/event-stream" in response.headers.get("content-type", ""):
            response = httpx.Response(
                status_code=response.status_code,
                headers=dict(response.headers),
                stream=_FirstToolCallStream(response.stream),  # type: ignore[arg-type]
            )
        return response


def _load_system_base() -> str:
    """Load agent system prompt from disk (prompts/agent_system.md), no caching so edits take effect live."""
    try:
        from ..processing.prompts import PROMPTS_DIR, _strip_frontmatter

        path = PROMPTS_DIR / "agent_system.md"
        return _strip_frontmatter(path.read_text(encoding="utf-8")).strip()
    except Exception as exc:
        log.warning("Could not load agent_system.md, using fallback: %s", exc)
        return (
            "You are an intelligent research assistant embedded in Meridian, an ML paper exploration tool.\n"
            "Use tools whenever they improve accuracy. "
            "For paper content use rag_query(paper_id=..., question=...). "
            "For plots use execute_python(). For reports use create_dashboard()."
        )


_meridian_cache: tuple[float, str] | None = None  # (mtime, content)


def _load_meridian_context() -> str:
    """Load MERIDIAN.md with mtime-based caching — only re-reads from disk when the file changes."""
    global _meridian_cache
    try:
        from ..processing.prompts import PROMPTS_DIR, _strip_frontmatter

        path = PROMPTS_DIR / "MERIDIAN.md"
        if not path.exists():
            return ""
        mtime = path.stat().st_mtime
        if _meridian_cache and _meridian_cache[0] == mtime:
            return _meridian_cache[1]
        content = _strip_frontmatter(path.read_text(encoding="utf-8")).strip()
        _meridian_cache = (mtime, content)
        return content
    except Exception as exc:
        log.warning("Could not load MERIDIAN.md: %s", exc)
        return ""


def _load_skills(user_text: str) -> str:
    """Scan skills/ for prompt files whose triggers match the user message; return injections."""
    try:
        import re as _re

        from ..processing.prompts import PROMPTS_DIR, _strip_frontmatter

        skills_dir = PROMPTS_DIR / "skills"
        if not skills_dir.exists():
            return ""

        text_lower = user_text.lower()
        injections: list[str] = []

        for path in sorted(skills_dir.glob("*.md")):
            raw = path.read_text(encoding="utf-8")
            # Read triggers from frontmatter (comma-separated)
            m = _re.search(r"^triggers:\s*(.+)$", raw, _re.MULTILINE)
            if not m:
                continue
            triggers = [t.strip() for t in m.group(1).split(",") if t.strip()]
            if any(t in text_lower for t in triggers):
                body = _strip_frontmatter(raw).strip()
                if body:
                    injections.append(body)
                    log.debug("Injected skill: %s", path.stem)

        return "\n\n".join(injections)
    except Exception as exc:
        log.warning("_load_skills error: %s", exc)
        return ""


def _load_agent_instructions(name: str, fallback: str = "") -> str:
    """Load sub-agent instructions from prompts/agents/{name}.md — no caching, edits take effect live."""
    try:
        from ..processing.prompts import PROMPTS_DIR, _strip_frontmatter

        path = PROMPTS_DIR / "agents" / f"{name}.md"
        if path.exists():
            return _strip_frontmatter(path.read_text(encoding="utf-8")).strip()
        log.warning("agents/%s.md not found, using fallback", name)
    except Exception as exc:
        log.warning("Could not load agents/%s.md: %s", name, exc)
    return fallback


def _build_provider(settings):
    """Return an OpenAIProvider for the configured backend.

    The OpenAI Agents SDK has two underlying transports:
      - Responses API  (/responses)      — OpenAI-only; use_responses=True
      - Chat completions (/chat/completions) — universal; use_responses=False

    DeepSeek, Gemini, and any other third-party provider must use
    use_responses=False because they only implement /chat/completions.
    vLLM and Ollama are also /chat/completions only.
    """
    from agents.models.openai_provider import OpenAIProvider

    provider = settings.llm.provider
    if provider == "vllm":
        base_url = settings.llm.vllm_base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"
        api_key = "EMPTY"
    elif provider == "deepseek":
        base_url = "https://api.deepseek.com/v1"
        api_key = settings.deepseek_api_key or "not-set"
    elif provider == "gemini":
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
        api_key = settings.gemini_api_key or "not-set"
    else:
        # ollama (and anthropic as a fallback — Anthropic has no OpenAI-compat agent endpoint)
        if provider == "anthropic":
            import logging as _log

            _log.getLogger(__name__).warning(
                "Anthropic is not OpenAI-Agents-SDK compatible for the agent loop. "
                "Falling back to Ollama. Switch to vllm/ollama/deepseek/gemini for agent chat."
            )
        base_url = settings.llm.ollama_base_url.rstrip("/") + "/v1"
        api_key = "not-needed"

    # All non-OpenAI providers use /chat/completions, not /responses
    if getattr(settings.agent, "strip_parallel_tool_calls", True):
        from openai import AsyncOpenAI

        _transport = _SingleToolCallTransport(httpx.AsyncHTTPTransport())
        _http = httpx.AsyncClient(transport=_transport)
        _oai = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=_http)
        return OpenAIProvider(openai_client=_oai, use_responses=False)

    return OpenAIProvider(base_url=base_url, api_key=api_key, use_responses=False)


def _instrument_fn(fn, agent_name: str, event_queue: asyncio.Queue):
    """Wrap a sync tool function as async, emitting tool_call/tool_result events into the queue."""
    import functools
    import inspect
    import uuid

    sig = inspect.signature(fn)

    async def instrumented(*args, **kwargs):
        call_id = uuid.uuid4().hex[:12]
        try:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            tool_input = dict(bound.arguments)
        except Exception:
            tool_input = kwargs

        await event_queue.put(
            {
                "type": "tool_call",
                "id": call_id,
                "tool": fn.__name__,
                "input": tool_input,
                "agent": agent_name,
            }
        )

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: fn(*args, **kwargs))
        except Exception as exc:
            result = f"Error: {exc}"

        await event_queue.put(
            {
                "type": "tool_result",
                "id": call_id,
                "tool": fn.__name__,
                "content": str(result),
                "agent": agent_name,
            }
        )
        return result

    functools.update_wrapper(instrumented, fn)
    instrumented.__signature__ = sig  # preserve original schema for function_tool
    return instrumented


def _build_orchestrator(system: str, settings, event_queue: asyncio.Queue):
    """Build the Meridian orchestrator.

    Architecture:
      - Research/lookup tools live directly on the orchestrator for single-step queries.
      - Three sub-agents handle multi-step pipelines: Processing, Knowledge, Analysis.
      - Sub-agent instructions are loaded from prompts/agents/*.md (live-reloadable).
    """
    from agents import Agent, ModelSettings, function_tool

    from .agent_tools import (
        add_note,
        create_dashboard,
        embed_paper,
        execute_python,
        extract_entities,
        extract_pdf_text,
        find_similar_papers,
        get_entities,
        get_paper,
        graph_traverse,
        ingest_papers,
        ingest_webpage,
        ingest_wikipedia_article,
        list_papers,
        list_workspace,
        ocr_paper,
        query_database,
        rag_query,
        search_papers,
        semantic_search,
        summarize_paper,
        update_meridian_context,
    )

    model = settings.llm.model_name

    def _i(fn, name):
        return _instrument_fn(fn, name, event_queue)

    # ── Sub-agents (instructions loaded from prompts/agents/*.md) ─────────────

    # parallel_tool_calls=False is applied to all agents — DeepSeek V4 ignores
    # this hint from the API but the SSE interceptor enforces it at transport level.
    _no_parallel = ModelSettings(parallel_tool_calls=False)

    processing_agent = Agent(
        name="Processing",
        instructions=_load_agent_instructions(
            "processing",
            fallback=(
                "You are the Processing specialist. Run paper processing pipelines.\n"
                "Tools: extract_pdf_text (fast text extraction), ocr_paper (vision OCR for scanned PDFs), "
                "summarize_paper (LLM summary, blocks up to 600s — never call more than once per paper).\n"
                "Pipeline: extract_pdf_text → summarize_paper. For scanned PDFs: ocr_paper → summarize_paper."
            ),
        ),
        tools=[
            function_tool(_i(summarize_paper, "Processing")),
            function_tool(_i(ocr_paper, "Processing")),
            function_tool(_i(extract_pdf_text, "Processing")),
        ],
        model=model,
        model_settings=_no_parallel,
    )

    knowledge_agent = Agent(
        name="Knowledge",
        instructions=_load_agent_instructions(
            "knowledge",
            fallback=(
                "You are the Knowledge specialist. Explore the knowledge graph and ingest new content.\n"
                "Tools: graph_traverse (BFS walk of citation/semantic edges), "
                "get_entities / extract_entities (structured entity extraction), "
                "ingest_wikipedia_article (add Wikipedia articles), ingest_webpage (screenshot-OCR web pages).\n"
                "For entities: call get_entities first; if empty, call extract_entities then get_entities again."
            ),
        ),
        tools=[
            function_tool(_i(graph_traverse, "Knowledge")),
            function_tool(_i(get_entities, "Knowledge")),
            function_tool(_i(extract_entities, "Knowledge")),
            function_tool(_i(ingest_wikipedia_article, "Knowledge")),
            function_tool(_i(ingest_webpage, "Knowledge")),
        ],
        model=model,
        model_settings=_no_parallel,
    )

    analysis_agent = Agent(
        name="Analysis",
        instructions=_load_agent_instructions(
            "analysis",
            fallback=(
                "You are the Analysis and Visualisation specialist.\n"
                "Tools: execute_python (computation, plots — WORKSPACE variable pre-defined), "
                "create_dashboard (HTML reports served to browser — NEVER use execute_python for HTML), "
                "list_workspace (list previously created files).\n"
                "Rule: any HTML or dashboard → create_dashboard() only."
            ),
        ),
        tools=[
            function_tool(_i(execute_python, "Analysis")),
            function_tool(_i(create_dashboard, "Analysis")),
            function_tool(_i(list_workspace, "Analysis")),
        ],
        model=model,
        model_settings=_no_parallel,
    )

    # ── Orchestrator — direct tools + sub-agent delegation ────────────────────

    return Agent(
        name="Meridian",
        instructions=system,
        model_settings=_no_parallel,
        tools=[
            # Direct research/lookup tools — use these for single-step queries
            function_tool(_i(search_papers, "Meridian")),
            function_tool(_i(semantic_search, "Meridian")),
            function_tool(_i(find_similar_papers, "Meridian")),
            function_tool(_i(list_papers, "Meridian")),
            function_tool(_i(get_paper, "Meridian")),
            function_tool(_i(rag_query, "Meridian")),
            function_tool(_i(query_database, "Meridian")),
            function_tool(_i(embed_paper, "Meridian")),
            function_tool(_i(ingest_papers, "Meridian")),
            function_tool(_i(graph_traverse, "Meridian")),
            function_tool(add_note),
            function_tool(update_meridian_context),
            # Sub-agents — delegate when chaining multiple related tool calls
            processing_agent.as_tool(
                tool_name="processing_expert",
                tool_description=(
                    "Run multi-step paper processing pipelines: "
                    "PDF text extraction → summarisation, or OCR → summarisation. "
                    "Use when summarising, OCR-ing, or extracting text from papers."
                ),
            ),
            knowledge_agent.as_tool(
                tool_name="knowledge_expert",
                tool_description=(
                    "Extract structured entities and relationships from papers, "
                    "or ingest Wikipedia articles and web pages into the corpus. "
                    "Do NOT use for graph traversal — call graph_traverse() directly instead."
                ),
            ),
            analysis_agent.as_tool(
                tool_name="analysis_expert",
                tool_description=(
                    "Run Python computations, generate plots, or create HTML dashboards. "
                    "Use for any calculation, visualisation, or multi-step analysis task."
                ),
            ),
        ],
        model=model,
    )


def _trim_history(history: list[dict], max_turns: int = 8) -> list[dict]:
    """Keep only the last max_turns complete exchanges (user → tools? → assistant).

    Counts user-role messages as turn boundaries. Cutting at a user message
    boundary guarantees we never split a function_call / function_call_output
    pair, which would produce an 'assistant tool_calls without matching tool
    messages' error on the next LLM request.
    """
    user_indices = [i for i, m in enumerate(history) if m.get("role") == "user"]
    if len(user_indices) <= max_turns:
        return history
    cut_at = user_indices[-max_turns]
    return history[cut_at:]


async def run_agent_stream(
    user_text: str,
    history: list[dict],
    paper_context: str | None,
    settings,
    enable_thinking: bool = True,
    images: list[str] | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Async generator — yields SSE event dicts.

    Args:
        user_text:       New user message text.
        history:         Prior turns: [{"role": "user"|"assistant", "content": str}].
        paper_context:   Active-paper block injected into system prompt (or None).
        settings:        Research-station Settings instance (provides LLM config).
        enable_thinking: When False, thinking delta events are suppressed.
    """
    try:
        from agents import RunConfig, Runner
        from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent
        from openai.types.responses import ResponseTextDeltaEvent
    except ImportError as exc:
        yield {"type": "error", "message": f"openai-agents not installed: {exc}"}
        return

    system = _load_system_base()

    meridian_ctx = _load_meridian_context()
    if meridian_ctx:
        system += "\n\n=== MERIDIAN CONTEXT ===\n" + meridian_ctx

    skills = _load_skills(user_text)
    if skills:
        system += "\n\n" + skills

    from pathlib import Path as _Path

    _workspace = _Path(__file__).resolve().parents[3] / "workspace"
    _workspace.mkdir(parents=True, exist_ok=True)
    system += f"\n\n=== ENVIRONMENT ===\nWORKSPACE path: {_workspace}\nUse this path (or the WORKSPACE variable in execute_python) for all file I/O."

    if paper_context:
        system += "\n\n=== ACTIVE PAPER ===\n" + paper_context

    provider = _build_provider(settings)
    run_config = RunConfig(model_provider=provider)

    # Queue receives inner tool events from sub-agents; drained between outer events.
    event_queue: asyncio.Queue = asyncio.Queue()
    agent = _build_orchestrator(system, settings, event_queue)

    # Build conversation input: trimmed history + new user turn
    if images:
        user_content: object = [{"type": "input_text", "text": user_text}] + [
            {"type": "input_image", "image_url": img} for img in images
        ]
    else:
        user_content = user_text
    messages: list[dict] = _trim_history(list(history), settings.agent.history_max_turns) + [
        {"role": "user", "content": user_content}
    ]

    # Track call_id → (tool_name, agent_name) for tool_result events
    call_tool_map: dict[str, tuple[str, str]] = {}

    async def _drain_queue():
        """Yield all queued sub-agent events without blocking."""
        await asyncio.sleep(0)  # let any pending event_queue.put() coroutines complete
        while not event_queue.empty():
            yield event_queue.get_nowait()

    try:
        result = Runner.run_streamed(
            agent,
            input=messages,
            run_config=run_config,
            max_turns=settings.agent.max_turns,
        )

        async for event in result.stream_events():
            # Drain any sub-agent inner events queued during the previous tool execution
            async for queued in _drain_queue():
                yield queued

            # ── Raw LLM streaming ──────────────────────────────────────────
            if isinstance(event, RawResponsesStreamEvent):
                etype = getattr(event.data, "type", "")

                # Live thinking stream (Qwen3, DeepSeek-R1 etc. via Ollama/vLLM)
                if etype == "response.reasoning_summary_text.delta":
                    if enable_thinking:
                        delta = getattr(event.data, "delta", "") or ""
                        if delta:
                            yield {"type": "thinking", "delta": delta}

                # Text content stream
                elif isinstance(event.data, ResponseTextDeltaEvent):
                    delta = event.data.delta or ""
                    if delta:
                        yield {"type": "content", "delta": delta}

            # ── Structured run items ───────────────────────────────────────
            elif isinstance(event, RunItemStreamEvent):
                if event.name == "tool_called":
                    ri = event.item.raw_item  # ResponseFunctionToolCall
                    tool_name = getattr(ri, "name", "unknown")
                    call_id = getattr(ri, "call_id", "")
                    agent_name = getattr(getattr(event.item, "agent", None), "name", "Meridian")
                    try:
                        tool_input = json.loads(getattr(ri, "arguments", "{}") or "{}")
                    except json.JSONDecodeError:
                        tool_input = {}
                    call_tool_map[call_id] = (tool_name, agent_name)
                    yield {
                        "type": "tool_call",
                        "id": call_id,
                        "tool": tool_name,
                        "input": tool_input,
                        "agent": agent_name,
                    }

                elif event.name == "tool_output":
                    raw = event.item.raw_item  # dict: {call_id, output, type}
                    call_id = raw.get("call_id", "") if isinstance(raw, dict) else ""
                    agent_name = getattr(getattr(event.item, "agent", None), "name", "Meridian")
                    tool_name, _ = call_tool_map.get(call_id, ("tool", "Meridian"))
                    output = str(event.item.output)
                    yield {
                        "type": "tool_result",
                        "id": call_id,
                        "tool": tool_name,
                        "content": output,
                        "agent": agent_name,
                    }

        # Final drain — catch any events queued on the last tool call
        async for queued in _drain_queue():
            yield queued

        # Record token usage from every SDK model response (bypasses our LLM clients)
        try:
            from ..processing.llm.base import LLMResponse
            from ..processing.llm.usage import record as _record_usage

            provider_name = settings.llm.provider
            model_name = settings.llm.model_name
            for raw_resp in result.raw_responses:
                u = getattr(raw_resp, "usage", None)
                if u is None:
                    continue
                _record_usage(
                    LLMResponse(
                        content="",
                        model=model_name,
                        provider=provider_name,
                        prompt_tokens=getattr(u, "input_tokens", None),
                        completion_tokens=getattr(u, "output_tokens", None),
                        generation_time_seconds=0.0,
                    ),
                    endpoint="agent",
                )
        except Exception:
            pass

    except Exception as exc:
        log.exception("agent_loop error")
        yield {"type": "error", "message": str(exc)}

    yield {"type": "done"}
