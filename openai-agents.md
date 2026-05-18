# OpenAI Agents SDK Architecture — Full Blueprint

> Extracted from the Meridian ML Research Station codebase.
> Use this as the reference architecture for building a separate repository with the same agent design.

---

## 1. Dependencies

```toml
# pyproject.toml
"openai-agents>=0.14.0",
```

```python
# Python imports used across the agent system
from agents import Agent, Runner, RunConfig, function_tool
from agents.models.openai_provider import OpenAIProvider
from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent
from openai.types.responses import ResponseTextDeltaEvent
```

The `openai-agents` SDK is the **only** agent framework dependency. No LangChain, CrewAI, or AutoGen. Everything is built on the SDK's primitives: `Agent`, `Runner.run_streamed()`, `function_tool`, and `agent.as_tool()`.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        User types in BottomChat.jsx                  │
│                            (frontend browser)                        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ POST /chats/{id}/messages/agent-stream
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  extras.py: agent_stream_chat()                                      │
│  ├─ Builds multi-turn history from DB                                │
│  ├─ Builds paper context block                                       │
│  ├─ Calls run_agent_stream()  ←─────────────────────────────────┐    │
│  └─ Persists tool_block + agent messages to DB                   │    │
└──────────────────────────────────────────────────────────────────┼────┘
                                                                   │
┌──────────────────────────────────────────────────────────────────┼────┐
│  agent_loop.py: run_agent_stream()  ← async generator            │    │
│  ├─ _load_system_base()         → prompts/agent_system.md        │    │
│  ├─ _load_meridian_context()    → prompts/MERIDIAN.md (cached)   │    │
│  ├─ _load_skills(user_text)     → prompts/skills/*.md (trigger)  │    │
│  ├─ _build_provider(settings)   → OpenAIProvider(vLLM/Ollama)    │    │
│  ├─ _build_orchestrator(...)    → Meridian + 4 sub-agents        │    │
│  │   ├─ Research sub-agent   (7 tools)                           │    │
│  │   ├─ Processing sub-agent (4 tools)                           │    │
│  │   ├─ Knowledge sub-agent  (5 tools)                           │    │
│  │   ├─ Analysis sub-agent   (3 tools)                           │    │
│  │   ├─ add_note                  (direct tool)                  │    │
│  │   └─ update_meridian_context   (direct tool)                  │    │
│  └─ Runner.run_streamed(agent, input=messages, max_turns=30)     │    │
│       └─ Yields SSE events: thinking, content, tool_call,        │    │
│                             tool_result, done, error              │    │
└──────────────────────────────────────────────────────────────────┘────┘
```

**Key insight:** There are two separate LLM client stacks:

| Stack | Used for | Provider |
|-------|----------|----------|
| `processing/llm/factory.py` | Non-agent summarization, plain chat | Anthropic, OpenAI, vLLM, Ollama |
| `agent_loop.py:_build_provider()` | Agent chat loop | **Only** vLLM or Ollama (via `OpenAIProvider`) |

The agent loop does **not** use the LLM clients from `processing/llm/`. It constructs its own `OpenAIProvider` directly from the `openai-agents` SDK.

---

## 3. The Agent Loop (`agent_loop.py`)

### 3.1 Entry Point: `run_agent_stream()`

This is the top-level async generator that produces SSE events. Full flow:

```python
async def run_agent_stream(
    user_text: str,
    history: list[dict],
    paper_context: str | None,
    settings,
    enable_thinking: bool = True,
) -> AsyncGenerator[dict, None]:
    # 1. Load system prompt from disk (no caching — live edits work immediately)
    system = _load_system_base()

    # 2. Load persistent agent memory (mtime-cached from MERIDIAN.md)
    meridian_ctx = _load_meridian_context()
    if meridian_ctx:
        system += "\n\n=== MERIDIAN CONTEXT ===\n" + meridian_ctx

    # 3. Inject skill prompts based on keyword triggers in user_text
    skills = _load_skills(user_text)
    if skills:
        system += "\n\n" + skills

    # 4. Inject WORKSPACE path
    system += f"\n\n=== ENVIRONMENT ===\nWORKSPACE path: {_workspace}..."

    # 5. Inject active paper context (title, abstract, summary, cached text)
    if paper_context:
        system += "\n\n=== ACTIVE PAPER ===\n" + paper_context

    # 6. Build the OpenAI provider (Ollama or vLLM /v1 endpoint)
    provider = _build_provider(settings)
    run_config = RunConfig(model_provider=provider)

    # 7. Create an asyncio.Queue for sub-agent inner tool events
    event_queue: asyncio.Queue = asyncio.Queue()

    # 8. Build the orchestrator with 4 sub-agents as tools
    agent = _build_orchestrator(system, settings, event_queue)

    # 9. Build input: trimmed history + new user turn
    messages = _trim_history(list(history), settings.agent.history_max_turns) \
               + [{"role": "user", "content": user_text}]

    # 10. Run the agent loop via the SDK
    result = Runner.run_streamed(
        agent,
        input=messages,
        run_config=run_config,
        max_turns=settings.agent.max_turns,   # default 30
    )

    # 11. Stream events — see section 3.4
    async for event in result.stream_events():
        ...
```

### 3.2 Provider Construction: `_build_provider()`

The agent loop only supports local (free) backends:

```python
def _build_provider(settings):
    from agents.models.openai_provider import OpenAIProvider

    provider = settings.llm.provider
    if provider == "vllm":
        base_url = settings.llm.vllm_base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url += "/v1"
    else:
        # Ollama exposes an OpenAI-compatible endpoint at /v1
        base_url = settings.llm.ollama_base_url.rstrip("/") + "/v1"

    return OpenAIProvider(base_url=base_url, api_key="not-needed")
```

The `OpenAIProvider` from the SDK wraps any OpenAI-compatible `/v1/chat/completions` endpoint. This means the agent runs on any model exposed through vLLM or Ollama.

### 3.3 Orchestrator Construction: `_build_orchestrator()`

This is the heart of the multi-agent architecture. It creates **4 specialist sub-agents** and exposes them as tools to the top-level orchestrator:

```python
def _build_orchestrator(system: str, settings, event_queue: asyncio.Queue):
    from agents import Agent, function_tool
    from .agent_tools import (
        search_papers, semantic_search, find_similar_papers, list_papers,
        get_paper, rag_query, query_database,
        summarize_paper, ocr_paper, embed_paper, extract_pdf_text,
        graph_traverse, get_entities, extract_entities,
        ingest_wikipedia_article, ingest_webpage,
        execute_python, create_dashboard, list_workspace,
        add_note, update_meridian_context,
    )

    model = settings.llm.model_name

    # Helper: instrument a tool fn to emit events into the queue
    def _i(fn, name):
        return _instrument_fn(fn, name, event_queue)

    # ── Research Agent (7 tools) ──────────────────────────────────
    research_agent = Agent(
        name="Research",
        instructions=(
            "You are the Research specialist inside Meridian. Your job is to search, "
            "retrieve, and read papers from the local corpus.\n"
            "Use search_papers() for keyword queries.\n"
            "Use semantic_search() for concept-based queries.\n"
            "Use find_similar_papers() to get neighbours of a known paper.\n"
            "Use get_paper() for full metadata + summary.\n"
            "Use rag_query() to answer questions about a paper's content.\n"
            "Use query_database() for SQL queries against the papers table.\n"
            "Complete the task fully and return a concise, useful result."
        ),
        tools=[
            function_tool(_i(search_papers,       "Research")),
            function_tool(_i(semantic_search,      "Research")),
            function_tool(_i(find_similar_papers,  "Research")),
            function_tool(_i(list_papers,          "Research")),
            function_tool(_i(get_paper,            "Research")),
            function_tool(_i(rag_query,            "Research")),
            function_tool(_i(query_database,       "Research")),
        ],
        model=model,
    )

    # ── Processing Agent (4 tools) ────────────────────────────────
    processing_agent = Agent(
        name="Processing",
        instructions=(
            "You are the Processing specialist. Your job is to run "
            "paper processing pipelines.\n"
            "Use extract_pdf_text() for fast embedded-text extraction.\n"
            "Use ocr_paper() for vision-LLM OCR on scanned PDFs.\n"
            "Use embed_paper() to add a paper to the vector store.\n"
            "Use summarize_paper() to generate an LLM summary.\n"
            "NEVER call summarize_paper() more than once per paper per response."
        ),
        tools=[
            function_tool(_i(summarize_paper,   "Processing")),
            function_tool(_i(ocr_paper,         "Processing")),
            function_tool(_i(embed_paper,       "Processing")),
            function_tool(_i(extract_pdf_text,  "Processing")),
        ],
        model=model,
    )

    # ── Knowledge Agent (5 tools) ────────────────────────────────
    knowledge_agent = Agent(
        name="Knowledge",
        instructions=(
            "You are the Knowledge specialist. Your job is to explore "
            "the knowledge graph and ingest new content.\n"
            "Use graph_traverse() to walk citation or semantic edges.\n"
            "Use get_entities() / extract_entities() for entity extraction.\n"
            "Use ingest_wikipedia_article() to add a Wikipedia article.\n"
            "Use ingest_webpage() to screenshot-OCR a web page into the corpus."
        ),
        tools=[
            function_tool(_i(graph_traverse,          "Knowledge")),
            function_tool(_i(get_entities,            "Knowledge")),
            function_tool(_i(extract_entities,        "Knowledge")),
            function_tool(_i(ingest_wikipedia_article, "Knowledge")),
            function_tool(_i(ingest_webpage,           "Knowledge")),
        ],
        model=model,
    )

    # ── Analysis Agent (3 tools) ─────────────────────────────────
    analysis_agent = Agent(
        name="Analysis",
        instructions=(
            "You are the Analysis and Visualization specialist.\n"
            "Use execute_python() for computation, statistics, and matplotlib plots.\n"
            "Use create_dashboard() to produce HTML dashboards.\n"
            "Never use execute_python() for HTML."
        ),
        tools=[
            function_tool(_i(execute_python,    "Analysis")),
            function_tool(_i(create_dashboard,  "Analysis")),
            function_tool(_i(list_workspace,    "Analysis")),
        ],
        model=model,
    )

    # ── Top-level Orchestrator ───────────────────────────────────
    return Agent(
        name="Meridian",
        instructions=system,
        tools=[
            # Sub-agents exposed as tools via `.as_tool()`
            research_agent.as_tool(
                tool_name="research_expert",
                tool_description="Search for papers, retrieve paper details, "
                                 "run RAG queries, or query the database.",
            ),
            processing_agent.as_tool(
                tool_name="processing_expert",
                tool_description="Run paper processing pipelines: PDF text extraction, "
                                 "OCR, embedding, or LLM summarization.",
            ),
            knowledge_agent.as_tool(
                tool_name="knowledge_expert",
                tool_description="Traverse the citation/semantic graph, extract entities, "
                                 "or ingest Wikipedia/web pages.",
            ),
            analysis_agent.as_tool(
                tool_name="analysis_expert",
                tool_description="Run Python computations/plots or create HTML dashboards "
                                 "and visualizations.",
            ),
            # Direct tools (no sub-agent delegation)
            function_tool(add_note),
            function_tool(update_meridian_context),
        ],
        model=model,
    )
```

### 3.4 SSE Event Streaming Loop

The streaming loop in `run_agent_stream()` is the bridge between the OpenAI Agents SDK event model and the frontend. It processes two distinct event types and also handles sub-agent tool event nesting:

```python
# Queue receives inner tool events from sub-agents; drained between outer events.
event_queue: asyncio.Queue = asyncio.Queue()

result = Runner.run_streamed(agent, input=messages, run_config=run_config, max_turns=30)

async for event in result.stream_events():
    # ── Drain sub-agent inner events queued during the previous tool execution ──
    async for queued in _drain_queue():
        yield queued

    # ── Raw LLM streaming ──────────────────────────────────────────
    if isinstance(event, RawResponsesStreamEvent):
        etype = getattr(event.data, "type", "")

        # Live thinking stream (Qwen3, DeepSeek-R1 etc.)
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

    # ── Structured run items (tool calls/results) ──────────────────
    elif isinstance(event, RunItemStreamEvent):
        if event.name == "tool_called":
            ri = event.item.raw_item
            tool_name = getattr(ri, "name", "unknown")
            call_id   = getattr(ri, "call_id", "")
            agent_name = getattr(getattr(event.item, "agent", None), "name", "Meridian")
            try:
                tool_input = json.loads(getattr(ri, "arguments", "{}") or "{}")
            except json.JSONDecodeError:
                tool_input = {}
            yield {
                "type": "tool_call", "id": call_id,
                "tool": tool_name, "input": tool_input, "agent": agent_name,
            }

        elif event.name == "tool_output":
            raw = event.item.raw_item
            call_id  = raw.get("call_id", "")
            tool_name = call_tool_map.get(call_id, ("tool",))
            yield {
                "type": "tool_result", "id": call_id,
                "tool": tool_name, "content": str(event.item.output),
                "agent": agent_name,
            }

# ── Final drain ──
async for queued in _drain_queue():
    yield queued

yield {"type": "done"}
```

**SSE event contract:**

| Event | Fields | Purpose |
|-------|--------|---------|
| `thinking` | `delta: str` | Model's chain-of-thought tokens |
| `content` | `delta: str` | Response text tokens |
| `tool_call` | `id, tool, input, agent` | Tool invocation started |
| `tool_result` | `id, tool, content, agent` | Tool returned result |
| `done` | *(none)* | Stream complete |
| `error` | `message: str` | Fatal error |

#### How sub-agent tool events work

The `callback` pattern causes the orchestrator's `.as_tool()` calls to disappear from the outer event stream — the SDK only surfaces the orchestrator's own tool calls (`research_expert`, `processing_expert`, etc.), not the inner calls those sub-agents make.

To preserve sub-agent tool call visibility, the code uses an `asyncio.Queue`:

1. Every tool function inside a sub-agent is wrapped with `_instrument_fn()`, which pushes `tool_call` and `tool_result` events into the shared `event_queue`
2. Between each outer event iteration, the queue is drained
3. The frontend receives the full nested structure and renders it as an expandable tree

```python
def _instrument_fn(fn, agent_name: str, event_queue: asyncio.Queue):
    """Wrap a sync tool function as async, emitting tool_call/tool_result events."""
    import functools, inspect, uuid

    sig = inspect.signature(fn)

    async def instrumented(*args, **kwargs):
        call_id = uuid.uuid4().hex[:12]
        try:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            tool_input = dict(bound.arguments)
        except Exception:
            tool_input = kwargs

        await event_queue.put({
            "type": "tool_call", "id": call_id,
            "tool": fn.__name__, "input": tool_input, "agent": agent_name,
        })

        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            result = f"Error: {exc}"

        await event_queue.put({
            "type": "tool_result", "id": call_id,
            "tool": fn.__name__, "content": str(result), "agent": agent_name,
        })
        return result

    functools.update_wrapper(instrumented, fn)
    instrumented.__signature__ = sig
    return instrumented
```

### 3.5 History Trimming

Keeps context windows manageable on local models (8 turn-pairs = 16 messages default):

```python
def _trim_history(history: list[dict], max_turns: int = 8) -> list[dict]:
    """Keep only the last max_turns complete user/assistant pairs."""
    if len(history) <= max_turns * 2:
        return history
    keep = max_turns * 2
    return history[-keep:]
```

---

## 4. Agent Tools (`agent_tools.py`)

### 4.1 Tool Architecture

All 21 tools follow a consistent pattern:

```python
def tool_name(param1: str, param2: int = 5, param3: str | None = None) -> str:
    """Docstring describing what the tool does.

    More detailed description. These docstrings become the tool's
    description in the JSON schema sent to the model.

    Args:
        param1: Description.
        param2: Description (default 5).
        param3: Optional parameter.
    """
    try:
        # Tool implementation
        with _db() as db:
            ...
        return "formatted text result"
    except Exception as exc:
        log.exception("tool_name")
        return f"Error: {exc}"
```

**Key characteristics:**
- All tools are **plain sync Python functions** (no decorators, no async)
- Type hints + docstrings auto-generate the JSON schema the model sees
- `@function_tool` is applied at registration time (in `AGENT_TOOLS` list or sub-agent construction)
- All tools return strings (the model reads them as tool output)
- Error handling: catch all, log, return error string

### 4.2 Agent Tools (registered via `agent.as_tool()`)

21 tool implementations are imported from `agent_tools.py` into the orchestrator:

```python
from .agent_tools import (
    search_papers,            # Keyword search on title + abstract
    semantic_search,          # Vector similarity search via ChromaDB
    find_similar_papers,      # Embedding neighbors of a known paper
    list_papers,              # Chronological list with filters
    get_paper,                # Full metadata + summary; auto-ingests arXiv IDs
    rag_query,                # BM25 retrieval over text chunks
    query_database,           # Read-only SQL (SELECT/WITH only)
    summarize_paper,          # Trigger LLM summary, block/poll until done
    ocr_paper,                # Vision-LLM OCR trigger
    embed_paper,              # Add paper to ChromaDB vector store
    extract_pdf_text,         # PyMuPDF text extraction (instant)
    graph_traverse,           # BFS walk through citation/semantic graph
    get_entities,             # Structured entities + relationships
    extract_entities,         # Trigger entity extraction (~20s)
    ingest_wikipedia_article, # Fetch Wikipedia, cache, create corpus entry
    ingest_webpage,           # Screenshot + vision OCR → web: paper
    execute_python,           # Sandboxed Python execution
    create_dashboard,         # Write HTML to workspace/
    list_workspace,           # List agent-created dashboards
    add_note,                 # Attach research note to paper
    update_meridian_context,  # Rewrite persistent memory file
)
```

### 4.3 Complete Tool List with Registrations

```
Orchestrator (Meridian)
├── research_expert    ←  research_agent.as_tool()
│   ├── search_papers
│   ├── semantic_search
│   ├── find_similar_papers
│   ├── list_papers
│   ├── get_paper
│   ├── rag_query
│   └── query_database
├── processing_expert  ←  processing_agent.as_tool()
│   ├── summarize_paper
│   ├── ocr_paper
│   ├── embed_paper
│   └── extract_pdf_text
├── knowledge_expert   ←  knowledge_agent.as_tool()
│   ├── graph_traverse
│   ├── get_entities
│   ├── extract_entities
│   ├── ingest_wikipedia_article
│   └── ingest_webpage
├── analysis_expert    ←  analysis_agent.as_tool()
│   ├── execute_python
│   ├── create_dashboard
│   └── list_workspace
├── add_note           ← function_tool(direct)
└── update_meridian_context ← function_tool(direct)
```

### 4.4 Complete Call Flow: User Query → Sub-Agent → Tool → Result

Here is the exact sequence when a user asks *"Find papers about transformer efficiency and summarize the top one"*:

**Step 1: User sends message via frontend**
```javascript
// BottomChat.jsx — sendAgent("Find papers about transformer efficiency and summarize the top one")
abortRef.current = window.api.streamAgentMessage(chatId, text, paper?.id, thinkingEnabled, {
    onThinking: (delta) => { thinkingAcc += delta; updateLast({ thinking: thinkingAcc }); },
    onContent: (delta)  => { contentAcc += delta;  updateLast({ text: contentAcc }); },
    onToolCall: ({ id, tool, input, agent }) => {
        // "research_expert" → create SubAgentPill (blue, #5a8af0)
        // Inner web.expert tool calls → nested inside research_expert's children[]
    },
    onToolResult: ({ id, tool, content, agent }) => {
        // Update result in the corresponding pill
    },
    onDone: () => { ... },
});
```

**Step 2: Frontend sends POST to SSE endpoint**
```javascript
// api.js — streamAgentMessage()
fetch(`${base}/chats/${chatId}/messages/agent-stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, paper_id: paperId, thinking }),
    signal: controller.signal,
}).then(async (res) => {
    const reader = res.body.getReader();
    // SSE line-by-line parsing...
    const ev = JSON.parse(payload);
    if (ev.type === "tool_call") onToolCall({ id: ev.id, tool: ev.tool, input: ev.input, agent: ev.agent });
    if (ev.type === "tool_result") onToolResult({ id: ev.id, tool: ev.tool, content: ev.content, agent: ev.agent });
});
```

**Step 3: FastAPI endpoint builds context and triggers agent**
```python
@router.post("/chats/{chat_id}/messages/agent-stream")
async def agent_stream_chat(chat_id: str, body: dict, db: Session = Depends(get_db)):
    text = body.get("text")
    paper_id = body.get("paper_id")
    settings = get_settings()

    # Build multi-turn history from DB
    history = []
    for m in chat.messages[-40:]:
        if m.role == "tool_block":
            # Expand stored tool_calls back into OpenAI format
            tool_data = json.loads(m.content)
            history.append({"role": "assistant", "content": None, "tool_calls": [...]})
            history.append({"role": "tool", "tool_call_id": ..., "content": ...})
        elif m.role in ("user", "agent"):
            history.append({"role": "assistant" if m.role == "agent" else "user", "content": m.content})

    paper_ctx = _build_paper_context(paper_id, db) if paper_id else None

    from ..agent_loop import run_agent_stream

    async def event_gen():
        async for event in run_agent_stream(
            user_text=text,
            history=history,
            paper_context=paper_ctx,
            settings=settings,
            enable_thinking=True,
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")
```

**Step 4: Agent loop loads prompts, builds orchestrator, runs stream**
```python
# Inside run_agent_stream()
system = _load_system_base()           # prompts/agent_system.md — live reload
system += _load_meridian_context()     # persistent agent memory
system += _load_skills(user_text)      # skill injections by keyword triggers
system += paper_context                # active paper metadata

provider = _build_provider(settings)   # OpenAIProvider → vLLM/Ollama
agent = _build_orchestrator(system, settings, event_queue)

# The orchestrator sees the user message and delegates:
# 1. Calls research_expert → which internally calls search_papers("transformer efficiency")
# 2. Gets result back from research_expert
# 3. Calls processing_expert → which internally calls summarize_paper("arxiv:2304.00001")
# 4. Gets result back from processing_expert
# 5. Synthesizes final response text

result = Runner.run_streamed(agent, input=messages, run_config=run_config, max_turns=30)
```

**Step 5: During the agent run, events stream to the frontend**

The event sequence for this request would be:

```
Streaming content:
  → {type: "content", delta: "Let"}      (orchestrator thinking)
  → {type: "content", delta: " me"}      (orchestrator writing)
  → {type: "content", delta: " search..."}

Tool call (outer):
  → {type: "tool_call", id: "abc123", tool: "research_expert", input: {}, agent: "Meridian"}

  (queue drain — inner tools from Research sub-agent):
  → {type: "tool_call",  id: "def456", tool: "search_papers",         input: {query: "transformer efficiency", limit: 10}, agent: "Research"}
  → {type: "tool_call",  id: "ghi789", tool: "semantic_search",      input: {query: "transformer efficiency", limit: 8}, agent: "Research"}
  → {type: "tool_result", id: "def456", tool: "search_papers",         content: "Found 5 papers...", agent: "Research"}
  → {type: "tool_result", id: "ghi789", tool: "semantic_search",      content: "Top 8 papers...", agent: "Research"}

Tool result (outer):
  → {type: "tool_result", id: "abc123", tool: "research_expert", content: "(sub-agent result)", agent: "Meridian"}

Tool call (outer):
  → {type: "tool_call", id: "jkl012", tool: "processing_expert", input: {}, agent: "Meridian"}

  (queue drain — inner tools from Processing sub-agent):
  → {type: "tool_call",  id: "mno345", tool: "summarize_paper", input: {paper_id: "arxiv:2304.00001"}, agent: "Processing"}
  → (… 60s of polling …)
  → {type: "tool_result", id: "mno345", tool: "summarize_paper", content: "Summary ready (took ~62s). TL;DR: ...", agent: "Processing"}

Tool result (outer):
  → {type: "tool_result", id: "jkl012", tool: "processing_expert", content: "(sub-agent result)", agent: "Meridian"}

Streaming content:
  → {type: "content", delta: "Here's what I found..."}
  → {type: "content", delta: "..."}

Done:
  → {type: "done"}
```

**Step 6: Frontend renders the tool calls with nesting**

```jsx
// BottomChat.jsx — onToolCall handler builds nesting structure
onToolCall: ({ id, tool, input, agent }) => {
    const isOrchestrator = !agent || agent === "Meridian";
    if (isOrchestrator && SUB_AGENTS.has(tool)) {
        // Create sub-agent container (e.g., research_expert)
        toolsMap[id] = {
            id, tool, input, result: null,
            streaming: true, type: "sub_agent", children: [],
        };
    } else if (!isOrchestrator) {
        // Inner tool call — nest under most recent open sub-agent
        const openSA = Object.values(toolsMap).reverse()
            .find(e => e.type === "sub_agent" && e.streaming);
        if (openSA) {
            childMap[id] = openSA.id;
            openSA.children = [...openSA.children, {
                id, tool, input, result: null, streaming: true,
            }];
        }
    } else {
        // Direct orchestrator tool (add_note, update_meridian_context)
        toolsMap[id] = { id, tool, input, result: null, streaming: true };
    }
    updateLast({ tools: Object.values(toolsMap) });
},
```

This produces a visual tree in the chat:

```
🔵 research_expert
   ├─ search_papers("transformer efficiency") ✓
   └─ semantic_search("transformer efficiency") ✓
🟠 processing_expert
   └─ summarize_paper("arxiv:2304.00001") ⏳… ✓
```

### 4.5 Key Tool Patterns

#### Pattern 1: HTTP delegation to local API

Many tools delegate to the local FastAPI server via `_http()`, avoiding DB access duplication:

```python
def embed_paper(paper_id: str) -> str:
    try:
        _http(f"/papers/{urllib.parse.quote(paper_id, safe='')}/embed", method="POST")
        return "Embedding queued for '{paper_id}'."
    except urllib.error.HTTPError as exc:
        return f"API error {exc.code}: {detail}"

def summarize_paper(paper_id: str) -> str:
    # Trigger via HTTP
    _http(f"/papers/{enc}/reader/regenerate", method="POST")
    # Poll until done
    while True:
        time.sleep(5)
        prog = _http(f"/papers/{enc}/summarise/progress")
        if not prog.get("active"):
            # Check DB for result
            if summ:
                return "Summary ready..."
```

#### Pattern 2: Lazy singleton initialization

All heavy dependencies (DB session factory, embedding service, settings) are lazy-loaded:

```python
_session_factory = None
_embed_svc = None

def _db():
    global _session_factory
    if _session_factory is None:
        from ..config.settings import get_settings
        from ..database.engine import build_engine, build_session_factory
        s = get_settings()
        _session_factory = build_session_factory(build_engine(s.database.sqlite_path))
    from ..database.engine import get_session
    return get_session(_session_factory)

def _embed():
    global _embed_svc
    if _embed_svc is None:
        from ..processing.embedding_service import get_embedding_service
        _embed_svc = get_embedding_service()
    return _embed_svc
```

#### Pattern 3: Auto-ingest on demand

`get_paper()` automatically ingests papers from arXiv if they're not yet in the database:

```python
def get_paper(paper_id: str, include_summary: bool = True) -> str:
    # Check DB first
    with _db() as db:
        exists = db.get(PaperORM, paper_id) is not None

    if not exists:
        found = _auto_ingest(paper_id)   # → fetch from arXiv, enrich, store, queue PDF+embed
        if not found:
            return f"Paper '{paper_id}' not found in database."
    ...
```

---

## 5. Prompt & Skill System

### 5.1 Live-Reload System Prompt

The main system prompt (`prompts/agent_system.md`) is **re-read from disk on every request** — no caching, no restart needed:

```python
def _load_system_base() -> str:
    try:
        from ..processing.prompts import PROMPTS_DIR, _strip_frontmatter
        path = PROMPTS_DIR / "agent_system.md"
        return _strip_frontmatter(path.read_text(encoding="utf-8")).strip()
    except Exception as exc:
        log.warning("Could not load agent_system.md, using fallback: %s", exc)
        return "You are an intelligent research assistant..."  # hardcoded fallback
```

### 5.2 Persistent Agent Memory

`prompts/MERIDIAN.md` acts as the agent's persistent memory. It's mtime-cached (re-read only when the file changes) and injected into every system prompt:

```python
_meridian_cache: tuple[float, str] | None = None

def _load_meridian_context() -> str:
    global _meridian_cache
    path = PROMPTS_DIR / "MERIDIAN.md"
    mtime = path.stat().st_mtime
    if _meridian_cache and _meridian_cache[0] == mtime:
        return _meridian_cache[1]
    content = _strip_frontmatter(path.read_text(encoding="utf-8")).strip()
    _meridian_cache = (mtime, content)
    return content
```

The agent can rewrite this file via `update_meridian_context()`:

```python
def update_meridian_context(content: str) -> str:
    path = PROMPTS_DIR / "MERIDIAN.md"
    frontmatter = "---\nname: MERIDIAN\ndescription: >\n  Living context file...\n---\n\n"
    path.write_text(frontmatter + content.strip() + "\n", encoding="utf-8")
    return "MERIDIAN.md updated. Changes will take effect on your next message."
```

### 5.3 Skill Injection by Keyword Trigger

Skill files in `prompts/skills/*.md` have YAML frontmatter with `triggers:` — comma-separated keywords. If any trigger appears in the user message, the skill body is injected into the system prompt:

```python
def _load_skills(user_text: str) -> str:
    text_lower = user_text.lower()
    injections: list[str] = []

    for path in sorted(skills_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        m = re.search(r"^triggers:\s*(.+)$", raw, re.MULTILINE)
        if not m:
            continue
        triggers = [t.strip() for t in m.group(1).split(",") if t.strip()]
        if any(t in text_lower for t in triggers):
            body = _strip_frontmatter(raw).strip()
            if body:
                injections.append(body)

    return "\n\n".join(injections)
```

Example skill frontmatter:
```yaml
---
name: dashboard_style
triggers: dashboard, html, visuali, chart, plot, d3, interactive, render, svg, figure, table, report
---
```

### 5.4 Prompt File Format

All prompt files use YAML frontmatter + markdown body:
```markdown
---
name: agent_system
description: >
  System prompt for the agentic chat loop.
variables: none
used_by: api/agent_loop.py
---

You are an intelligent research assistant embedded in Meridian...
```

The `_strip_frontmatter()` function strips the `---...---` block before use:
```python
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)

def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1)
```

### 5.5 Agent System Prompt Structure

The full system prompt assembled at runtime:

```
[agent_system.md body — ~150 lines of tool-use guidelines, routing rules]
=== MERIDIAN CONTEXT ===
[persistent memory from MERIDIAN.md — agent's notes about user preferences]

[skill injection if triggered — e.g., dashboard_style.md CSS design system]

=== ENVIRONMENT ===
WORKSPACE path: /home/.../ml_research_station/workspace
Use this path for all file I/O.

=== ACTIVE PAPER ===
Paper ID: arxiv:2301.00001
Title: Attention Is All You Need
Authors: Vaswani, Shazeer, Parmar, Uszkoreit, Jones, Gomez, Kaiser, Polosukhin
Venue: arxiv  Year: 2017
PDF downloaded: yes  Text extractable: yes  OCR/extracted text cached: yes
Abstract: ...
TLDR: ...
Methodology: ...
```

---

## 6. Configuration

### 6.1 Agent Settings

```python
class AgentSettings(BaseModel):
    max_turns: int = 30           # Max agent tool-calling turns per chat message
    history_max_turns: int = 8    # Prior turn-pairs to include in context window

# Env overrides:
# AGENT__MAX_TURNS=30
# AGENT__HISTORY_MAX_TURNS=8
```

### 6.2 LLM Settings (used by agent loop)

```python
class LLMSettings(BaseModel):
    provider: str = "anthropic"          # Only vllm | ollama used by agent loop
    model_name: str = "claude-sonnet-4-6"
    vllm_base_url: str = "http://localhost:8000/v1"
    ollama_base_url: str = "http://localhost:11434"
    max_tokens: int = 4096
    temperature: float = 0.1
    enable_thinking: bool = False        # Chain-of-thought reasoning
```

---

## 7. MCP Server (Claude Code Integration)

The `mcp_server/server.py` exposes 12 of the 21 agent tools to Claude Code via FastMCP (JSON-RPC stdio). It mirrors the same tool implementations but uses `@mcp.tool` instead of `@function_tool`:

```python
from fastmcp import FastMCP
mcp = FastMCP("research-station")

@mcp.tool
def search_papers(query: str, limit: int = 10, since_days: str | int | None = None,
                  source: str | None = None) -> str:
    """Keyword search across paper titles and abstracts in the local SQLite database."""
    # ... same implementation as agent_tools.py
```

Key differences from the web agent:
- Uses `@mcp.tool` decorator (FastMCP) instead of `@function_tool` (OpenAI Agents SDK)
- Has only 12 tools (no `execute_python`, `add_note`, `ingest_wikipedia_article`, `ingest_webpage`, `get_entities`, `extract_entities`, `graph_traverse`, `update_meridian_context`)
- `summarize_paper` is fire-and-forget (doesn't block/poll)
- `rag_query` uses simpler keyword scoring instead of BM25
- Uses `research_station.` prefixed imports (not relative)

---

## 8. Frontend Integration

### 8.1 SSE Client (`api.js`)

```javascript
streamAgentMessage(chatId, text, paperId = null, thinking = true,
                   { onThinking, onContent, onToolCall, onToolResult, onDone, onError } = {}) {
    const controller = new AbortController();
    fetch(`${base}/chats/${encodeURIComponent(chatId)}/messages/agent-stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, paper_id: paperId, thinking }),
        signal: controller.signal,
    }).then(async (res) => {
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const lines = buf.split("\n");
            buf = lines.pop();
            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                const ev = JSON.parse(line.slice(6).trim());
                if      (ev.type === "thinking")    onThinking(ev.delta);
                else if (ev.type === "content")     onContent(ev.delta);
                else if (ev.type === "tool_call")   onToolCall({ id: ev.id, tool: ev.tool, input: ev.input, agent: ev.agent });
                else if (ev.type === "tool_result") onToolResult({ id: ev.id, tool: ev.tool, content: ev.content, agent: ev.agent });
                else if (ev.type === "done")        onDone();
                else if (ev.type === "error")       onError(ev.message);
            }
        }
    });
    return { abort: () => controller.abort() };
}
```

### 8.2 Chat Component (`BottomChat.jsx`)

The `sendAgent()` function manages the full agent interaction:

```jsx
const sendAgent = async (text) => {
    // 1. Create optimistic message with tool array placeholder
    setMsgs(m => [...m,
        { role: "user", text },
        { role: "agent", text: "", thinking: "", tools: [], streaming: true },
    ]);

    // 2. Get chat ID (create if needed)
    chatId = await ensureChatId();

    // 3. Open SSE stream
    abortRef.current = window.api.streamAgentMessage(chatId, text, paper?.id, thinkingEnabled, {
        onThinking: (delta) => {
            thinkingAcc += delta;
            updateLast({ thinking: thinkingAcc });
        },
        onContent: (delta) => {
            contentAcc += delta;
            updateLast({ text: contentAcc });
        },
        onToolCall: ({ id, tool, input, agent }) => {
            // Handle sub-agent nesting — see section 8.3
        },
        onToolResult: ({ id, tool, content, agent }) => {
            // Update result in tool tree
        },
        onDone: () => {
            abortRef.current = null;
            if (Object.keys(toolsMap).length > 0 && window.refreshPapers) {
                setTimeout(window.refreshPapers, 500);  // refresh list (ingest may have added papers)
            }
        },
    });
};
```

### 8.3 Tool Call Nesting in Frontend

The frontend maintains `toolsMap` (call_id → entry) and `childMap` (child call_id → parent sub-agent id) to build the nested tree:

```jsx
const SUB_AGENTS = new Set(["research_expert", "processing_expert",
                             "knowledge_expert", "analysis_expert"]);

onToolCall: ({ id, tool, input, agent }) => {
    const isOrchestrator = !agent || agent === "Meridian";
    if (isOrchestrator && SUB_AGENTS.has(tool)) {
        // Sub-agent container (e.g., research_expert)
        toolsMap[id] = {
            id, tool, input, result: null,
            streaming: true, type: "sub_agent", children: [],
        };
    } else if (!isOrchestrator) {
        // Inner tool — nest under most recent open sub-agent
        const openSA = Object.values(toolsMap).reverse()
            .find(e => e.type === "sub_agent" && e.streaming);
        if (openSA) {
            childMap[id] = openSA.id;
            openSA.children.push({ id, tool, input, result: null, streaming: true });
        }
    }
    updateLast({ tools: Object.values(toolsMap) });
},

onToolResult: ({ id, tool, content, agent }) => {
    const parentId = childMap[id];
    if (parentId && toolsMap[parentId]) {
        // Update child within sub-agent
        toolsMap[parentId].children = toolsMap[parentId].children.map(c =>
            c.id === id ? { ...c, result: content, streaming: false } : c
        );
    } else if (toolsMap[id]) {
        toolsMap[id] = { ...toolsMap[id], result: content, streaming: false };
    }
    updateLast({ tools: Object.values(toolsMap) });

    // Side-effect: detect dashboard URLs
    const m = content.match(/https?:\/\/[^\s"'<>]+\/workspace\/[^\s"'<>]+\.html/);
    if (m) document.dispatchEvent(new CustomEvent("rs:dashboard-created", { detail: { url: m[0] } }));
},
```

### 8.4 Sub-Agent Color Coding (`AgentPanel.jsx`)

```jsx
function SubAgentPill({ col, tool, agentName, result, children }) {
    const COLORS = {
        "research_expert":    "#5a8af0",   // blue
        "processing_expert":  "#e07020",   // orange
        "knowledge_expert":   "#4db6ac",   // teal
        "analysis_expert":    "#a8c97a",   // green
    };
    const col = SUB_COLORS[tool] || "var(--ink-2)";
    // Renders an expandable pill with children rendered inside
}
```

---

## 9. Key Design Patterns to Reuse

### 9.1 Orchestrator + Sub-Agents via `.as_tool()`

The single most important pattern. Instead of one monolithic agent with 21 tools, split into specialists:

```python
specialist = Agent(name="SpecialistName", instructions="...", tools=[...], model=model)
orchestrator = Agent(
    name="Orchestrator",
    instructions=system,
    tools=[
        specialist.as_tool(
            tool_name="specialist_name",
            tool_description="Description the orchestrator uses to decide delegation.",
        ),
    ],
    model=model,
)
```

**Why this works:**
- The orchestrator sees fewer top-level tools (4 sub-agents + 2 direct = 6 tools)
- Each sub-agent has focused instructions + small tool list
- The sub-agent runs its own internal tool loop and returns a synthesized result
- Dramatically reduces hallucination on large tool lists

### 9.2 Tool Instrumentation for Event Visibility

The `_instrument_fn()` pattern makes inner tool calls visible despite the SDK's `as_tool()` callback abstraction:

```python
def _instrument_fn(fn, agent_name, event_queue):
    async def instrumented(*args, **kwargs):
        await event_queue.put({"type": "tool_call", ...})
        result = fn(*args, **kwargs)
        await event_queue.put({"type": "tool_result", ...})
        return result
    instrumented.__signature__ = inspect.signature(fn)
    return instrumented
```

### 9.3 Live-Reload Prompts from Disk

System prompts re-read on every request — no restart, no cache invalidation:

```python
def _load_system_base() -> str:
    path = PROMPTS_DIR / "agent_system.md"
    return _strip_frontmatter(path.read_text(encoding="utf-8")).strip()
```

### 9.4 Keyword-Triggered Skill Injection

Match user message against `triggers:` in skill frontmatter, inject matching skills:

```python
def _load_skills(user_text: str) -> str:
    text_lower = user_text.lower()
    for path in skills_dir.glob("*.md"):
        triggers = parse_frontmatter_triggers(path)
        if any(t in text_lower for t in triggers):
            injections.append(strip_frontmatter(path))
    return "\n\n".join(injections)
```

### 9.5 Lazy Singletons for Heavy Dependencies

Database sessions, embedding services, and config initialized on first use:

```python
_svc = None
def get_svc():
    global _svc
    if _svc is None:
        _svc = create_heavy_service()
    return _svc
```

### 9.6 SSE Streaming with asyncio.Queue Side Channel

Sub-agent tool events flow through an `asyncio.Queue` side channel, drained between outer event iterations:

```python
event_queue: asyncio.Queue = asyncio.Queue()

async for event in result.stream_events():
    async for queued in _drain_queue():  # drain side channel
        yield queued
    # process outer event
    yield outer_event

async for queued in _drain_queue():  # final drain
    yield queued
```

### 9.7 Plain Sync Tools with Type Hints for Schema Generation

The `openai-agents` SDK auto-generates JSON schemas from Python type hints and docstrings:

```python
def my_tool(query: str, limit: int = 10, source: str | None = None) -> str:
    """Search for things.
    
    Args:
        query: Search query string.
        limit: Max results (default 10).
        source: Optional filter.
    """
    # implementation
    return result
```

The SDK converts this into a tool schema with typed parameters, defaults, and enum descriptions — no manual JSON schema writing needed.

### 9.8 History Trimming for Context Management

Keep context windows bounded by dropping old turns while preserving complete pairs:

```python
def _trim_history(history, max_turns=8):
    if len(history) <= max_turns * 2:
        return history
    return history[-max_turns * 2:]
```

---

## 10. Complete File Map

| File | Role | Lines |
|------|------|-------|
| `src/research_station/api/agent_loop.py` | Core agent loop, orchestrator, SSE streaming, provider | 413 |
| `src/research_station/api/agent_tools.py` | 21 tool implementations, AGENT_TOOLS list | 1368 |
| `src/research_station/prompts/agent_system.md` | Main agent system prompt (live reload) | 152 |
| `src/research_station/prompts/MERIDIAN.md` | Persistent agent memory file | — |
| `src/research_station/prompts/skills/graph_traversal.md` | Skill: graph traversal (7-step workflow) | 318 |
| `src/research_station/prompts/skills/dashboard_style.md` | Skill: dashboard design system CSS | 169 |
| `src/research_station/prompts/chat_system.md` | Non-agentic chat system prompt | — |
| `src/research_station/processing/prompts.py` | Prompt loader (frontmatter stripping, template rendering) | 98 |
| `src/research_station/api/routes/extras.py` | Chat SSE endpoints (agent + plain, history, persistence) | lines 1990-2390 |
| `src/research_station/config/settings.py` | AgentSettings, LLMSettings | lines 94-134, 210-222 |
| `src/research_station/processing/llm/factory.py` | LLM client factory (non-agent summarization/chat) | 122 |
| `src/research_station/processing/llm/base.py` | BaseLLMClient protocol | 83 |
| `src/research_station/processing/llm/openai_compat.py` | OpenAI/vLLM chat client | 115 |
| `src/research_station/processing/llm/anthropic_client.py` | Anthropic chat client | 116 |
| `src/research_station/processing/llm/ollama.py` | Ollama chat client (streaming) | 262 |
| `mcp_server/server.py` | FastMCP stdio server (12 tools, Claude Code) | 608 |
| `frontend/components/BottomChat.jsx` | Chat drawer (agent/plain toggle, SSE consumption) | 388 |
| `frontend/components/AgentPanel.jsx` | Tool call pills, sub-agent pills, AgentMsg renderer | 1229 |
| `frontend/components/Prompts.jsx` | Prompt editor (view/edit agent prompts + skills) | 395 |
| `frontend/components/ApiConfigModal.jsx` | Settings modal (LLM/OCR/embed config) | 887 |
| `frontend/api.js` | HTTP/SSE client (`streamAgentMessage()` at line 310) | 581 |
| `pyproject.toml` | `openai-agents>=0.14.0` dependency | 48 |

---

## 11. Startup / Dependencies Summary

```bash
pip install "openai-agents>=0.14.0"

# For the MCP server:
pip install fastmcp

# For the LLM clients (non-agent path):
pip install httpx anthropic
```

Minimal agent-only dependencies for a fresh repo:
- `openai-agents` — the agent SDK (Agent, Runner, function_tool, as_tool, OpenAIProvider)
- `openai` — provides `ResponseTextDeltaEvent` type
- `asyncio` — for Queue side-channel and async streaming
- Any JSON-serializable backend (SQLite, Redis, in-memory) for history persistence
- Any HTTP framework (FastAPI, Flask, Starlette) for exposing the SSE endpoint
- Any prompt management strategy (files on disk, database, or env vars)
