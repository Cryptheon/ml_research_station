# Meridian — ML Research Station

> Project-level instructions for Claude Code. Loaded automatically at the start of every session.
> Keep this file up to date as the project evolves.

---

## What this project is

**Meridian** is a local ML paper exploration and research-assistant tool. It ingests papers from arXiv, bioRxiv, OpenReview, Semantic Scholar, Wikipedia, and arbitrary web pages, stores them in a local SQLite + ChromaDB database, and exposes them through a FastAPI backend + React SPA. An agentic chat loop (OpenAI Agents SDK, local vLLM/Ollama) lets users talk to the corpus with 22 callable tools.

---

## How to run

```bash
make serve           # Start API + frontend (hot-reload, http://localhost:8080)
make install-dev     # Install all deps (pdf, embeddings, api, llm, web, dev)
make lint            # ruff check + format
make typecheck       # mypy (strict)
make test            # pytest (all)
make test-unit       # pytest tests/unit/
make ingest          # CLI: pull new papers from all sources
```

The frontend is a React SPA served statically at `/` by FastAPI — **no build step**. Babel transpiles JSX in the browser. Edit `.jsx` files and reload.

The backend reads `.env` from the project root. Key variables:

```
LLM__PROVIDER=anthropic          # anthropic | openai | vllm | ollama
LLM__MODEL_NAME=claude-sonnet-4-6
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
SEMANTIC_SCHOLAR_API_KEY=...
```

---

## Directory layout

```
ml_research_station/
├── src/research_station/
│   ├── api/
│   │   ├── app.py              # FastAPI factory, lifespan, mounts, CORS
│   │   ├── agent_loop.py       # Agentic chat (OpenAI Agents SDK, streaming SSE)
│   │   ├── agent_tools.py      # 20 @function_tool implementations (~1300 lines)
│   │   ├── schemas.py          # Pydantic response schemas
│   │   ├── deps.py             # FastAPI dependencies
│   │   ├── jobs.py             # Background task management
│   │   └── routes/
│   │       ├── papers.py       # /papers/queue, /papers/{id}, /papers/velocity
│   │       ├── search.py       # /papers/search, /search/omni
│   │       ├── ingest.py       # /ingest/*, /ws/ingest/{id}
│   │       ├── users.py        # /users/me/* (pins, collections, chat, notes)
│   │       ├── system.py       # /system/health, /taxonomy/lanes, /sources
│   │       └── extras.py       # Reader, entities, OCR, summarise, traverse, web/ingest
│   ├── config/
│   │   └── settings.py         # Pydantic v2 Settings, singleton get_settings()
│   ├── database/
│   │   ├── engine.py           # SQLAlchemy engine (SQLite WAL), all ORM imports here
│   │   └── repository.py       # Typed CRUD (PaperRepository, CitationRepository, etc.)
│   ├── ingestion/
│   │   ├── pipeline.py         # Orchestrates fetch → enrich → store → download → embed
│   │   ├── arxiv_fetcher.py
│   │   ├── biorxiv_fetcher.py
│   │   ├── openreview_fetcher.py
│   │   ├── semantic_scholar.py
│   │   ├── openalex_enricher.py
│   │   ├── pdf_downloader.py
│   │   └── wikipedia_fetcher.py
│   ├── models/
│   │   ├── paper.py            # Paper, PaperORM, PaperSource, PaperStatus, cache flags
│   │   ├── summary.py          # PaperSummary, PaperSummaryORM
│   │   ├── entity.py           # PaperEntity, EntityRelationship
│   │   ├── taxonomy.py         # Fixed topic vocabulary
│   │   ├── user.py             # User, Collection, Watch, Chat, Pin (and ORMs)
│   │   └── web_link.py         # WebPaperLinkORM (web: paper → corpus paper links)
│   ├── processing/
│   │   ├── summarizer.py       # LLM-powered summarisation (map-reduce for long papers)
│   │   ├── entity_extractor.py # Structured entity + relationship extraction
│   │   ├── embedding_service.py# ChromaDB vector store, get_neighbors()
│   │   ├── pdf_ocr.py          # PyMuPDF text extraction + vision LLM OCR fallback
│   │   ├── web_ingest.py       # Playwright screenshot OCR → web:<hash> papers
│   │   └── llm/
│   │       ├── factory.py      # LLM client factory (anthropic/openai/vllm/ollama)
│   │       ├── anthropic_client.py
│   │       ├── openai_compat.py# Used for OpenAI API, vLLM, and OpenAI-compatible endpoints
│   │       └── ollama.py
│   └── prompts/
│       ├── agent_system.md     # Main agent persona + all tool-use guidelines (live reload)
│       ├── chat_system.md      # Non-agentic chat mode prompt
│       ├── summarizer_system.md
│       ├── summarizer_user.md
│       ├── entity_extract.md
│       ├── edge_classify.md
│       ├── ocr_page.md
│       ├── discover.md
│       └── skills/             # Dynamic skill injections (matched by trigger keywords)
│           ├── graph_traversal.md
│           └── dashboard_style.md
├── frontend/
│   ├── index.html              # Single HTML file, loads React UMD + Babel in-browser
│   ├── api.js                  # HTTP/WebSocket client (window.api)
│   ├── data.js                 # Mock data fallbacks
│   ├── styles.css              # CSS custom properties (dark/light, density, accent)
│   └── components/
│       ├── App.jsx             # Root: page routing, global state, polling
│       ├── LeftRail.jsx        # Paper queue, search, type/source filters, ingest panel
│       ├── CenterStage.jsx     # Paper reader, graph, knowledge graph highlight modes
│       ├── AgentPanel.jsx      # Context rail (entities, notes, screenshots, web links)
│       ├── BottomChat.jsx      # Dockable chat drawer (SSE streaming from agent loop)
│       ├── Library.jsx         # Collections, pins, chats, notebooks management
│       ├── IngestModal.jsx     # Ingest by arXiv ID / PDF upload / URL
│       ├── ApiConfigModal.jsx  # LLM provider + model config via UI
│       └── Prompts.jsx         # Prompt / skill editor
├── mcp_server/
│   └── server.py               # FastMCP stdio server — same tools as agent_tools.py
├── workspace/                  # Agent-written HTML dashboards (served at /workspace/)
├── data/
│   ├── papers.db               # SQLite database
│   ├── chroma/                 # ChromaDB vector store
│   ├── ocr/                    # Cached PDF text (per paper ID)
│   ├── pdfs/                   # Downloaded PDFs
│   └── web_screenshots/        # Viewport screenshots from web ingestion
├── Makefile
├── pyproject.toml
└── .env                        # Secrets and env overrides (git-ignored)
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.10+ |
| API framework | FastAPI + Uvicorn |
| ORM | SQLAlchemy (SQLite, WAL mode) |
| Vector DB | ChromaDB |
| Validation | Pydantic v2 |
| Agent SDK | `openai-agents` (OpenAI Agents SDK) |
| LLM providers | Anthropic, OpenAI, vLLM (local), Ollama (local) |
| Embeddings | vLLM / Ollama / sentence-transformers |
| OCR backends | DeepSeek-VL, Qwen-VL, NanonetsOCR (all vision LLMs) |
| Web scraping | Playwright (headless Chromium) + PIL (JPEG) |
| RAG | BM25 (`rank-bm25`) + ChromaDB semantic search |
| MCP server | FastMCP (stdio JSON-RPC) |
| Frontend | React 18 UMD + Babel in-browser (no build step) |
| HTTP client | httpx (async), fetch (browser) |
| Linter | ruff |
| Type checker | mypy (strict) |

---

## Core domain models

### Paper ID namespace

Papers have namespaced string IDs — never plain arXiv IDs:

| Prefix | Example | Source |
|---|---|---|
| `arxiv:` | `arxiv:2301.00001` | arXiv |
| `doi:` | `doi:10.1101/2021.01.01` | bioRxiv/DOI |
| `wikipedia:en:` | `wikipedia:en:Transformer_(machine_learning)` | Wikipedia |
| `web:` | `web:3f9a1c2e8b40` | Web page (sha256[:12] of URL) |

### PaperSource enum (`models/paper.py`)

`arxiv`, `biorxiv`, `openreview`, `semantic_scholar`, `manual`, `web`, `wikipedia`

### PaperStatus enum

`pending` → `downloaded` → `extracted` → `summarized` | `paywalled` | `error`

### Cache flags bitfield

| Bit | Value | Meaning |
|---|---|---|
| PDF | 1 | PDF downloaded to disk |
| Embeddings | 2 | Embedded in ChromaDB |
| Summary | 4 | LLM summary generated |
| Figures | 8 | Figures extracted |
| References | 16 | References parsed |
| Fulltext | 32 | Text extracted or OCR'd |

### PaperSummary fields

`tldr`, `contributions`, `methodology`, `key_results`, `limitations`, `related_work_context`, `interesting_aspects`, `suggested_follow_up`

---

## The agent loop

**File:** `src/research_station/api/agent_loop.py`

The web chat is powered by the **OpenAI Agents SDK** (`openai-agents` package), pointed at a local vLLM or Ollama endpoint (not the Anthropic API):

```python
provider = OpenAIProvider(base_url="http://localhost:8000/v1", api_key="not-needed")

agent = Agent(
    name="Meridian",
    instructions=system_prompt,   # loaded from prompts/agent_system.md on every call
    tools=AGENT_TOOLS,            # 20 @function_tool functions from agent_tools.py
    model=settings.llm.model_name,
)

result = Runner.run_streamed(
    agent,
    input=messages,
    run_config=RunConfig(model_provider=provider),
    max_turns=30,
)
```

**SSE event types streamed to the frontend:**

| Type | Payload | When |
|---|---|---|
| `thinking` | `delta: str` | Reasoning summary tokens |
| `content` | `delta: str` | Response text tokens |
| `tool_call` | `id, tool, input` | Tool invocation starts |
| `tool_result` | `id, tool, content` | Tool returns |
| `done` | — | Stream complete |

**Prompt loading:** `prompts/agent_system.md` is read from disk on **every** request — edits take effect immediately, no restart needed. Skills in `prompts/skills/*.md` are injected dynamically based on keyword triggers in the user message.

---

## Agent tools (`agent_tools.py`)

All tools are wrapped with `@function_tool`. Type hints + docstrings auto-generate the JSON schema shown to the model.

| Tool | What it does |
|---|---|
| `search_papers` | Keyword search on title + abstract |
| `semantic_search` | Vector similarity search (requires ChromaDB populated) |
| `find_similar_papers` | Graph neighbours of a known paper |
| `list_papers` | Chronological list (optional source/date filter) |
| `get_paper` | Full metadata + abstract + summary; auto-ingests arXiv IDs |
| `query_database` | Read-only SQL (SELECT/WITH only) |
| `summarize_paper` | Triggers LLM summary; blocks until done (up to 10 min, map-reduce for long papers) |
| `ocr_paper` | Vision LLM OCR for scanned PDFs |
| `embed_paper` | Adds paper to ChromaDB |
| `extract_pdf_text` | PyMuPDF text extraction (instant, no AI) |
| `rag_query` | BM25 retrieval over ~400-char text chunks |
| `graph_traverse` | Walk citation/semantic graph from a paper |
| `get_entities` | Structured entities + relationships for a paper |
| `extract_entities` | Trigger entity extraction (~20s) |
| `ingest_wikipedia_article` | Fetch Wikipedia, cache text, create `wikipedia:` paper |
| `ingest_webpage` | Screenshot + vision OCR → `web:` paper |
| `ingest_papers` | Batch-fetch papers from arXiv/bioRxiv/PubMed/OpenReview by keyword |
| `execute_python` | Sandboxed Python execution; `WORKSPACE` (pathlib.Path) pre-defined |
| `create_dashboard` | Write HTML to `workspace/{paper_subdir}/`; scoped to active paper |
| `list_workspace` | List agent-created dashboards, filtered to active paper |
| `add_note` | Attach research note to a paper |
| `update_meridian_context` | Rewrite `prompts/MERIDIAN.md`; injected into every agent prompt |

**Lazy singletons** in `agent_tools.py`: `_db()`, `_embed()`, `_cfg()` — these avoid repeated initialization.

---

## Key conventions

### Database access
- All DB access goes through `database/repository.py` repository classes — no raw SQL in business logic.
- New tables need their ORM class imported in `database/engine.py` (this triggers table creation on startup).

### Adding a new ORM model
1. Define the ORM class in `models/`
2. Import it in `database/engine.py` (`# noqa: F401`)
3. Ensure `PaperSource` enum in `models/paper.py` has a matching value if it's a new paper source type

### Adding a new agent tool
1. Write the function in `agent_tools.py` using `@function_tool`
2. Add it to the `AGENT_TOOLS` list at the bottom of the file
3. If the model needs guidance on when to use it, add a section to `prompts/agent_system.md`

### Adding a new API route
- Small helpers → `routes/extras.py`
- New domain area → new file under `routes/`, register in `app.py`

### Static file mounts (app.py)
- `/workspace/` → `workspace/` directory (agent dashboards)
- `/web-screenshots/` → `data/web_screenshots/` (OCR screenshots)

### Prompts
- Prompts live in `src/research_station/prompts/` as `.md` files with YAML frontmatter
- `prompts/agent_system.md` — main agent instructions; reloaded on every request
- `prompts/skills/*.md` — injected dynamically; frontmatter must include `triggers:` list
- Never hardcode prompt text in Python — always load from `.md` files via `processing/prompts.py`

### Settings
- All configuration is in `config/settings.py` as nested Pydantic groups
- Override via env vars using `__` delimiter: `LLM__MODEL_NAME=mistral-large`
- Access via singleton: `from research_station.config.settings import get_settings`

### Frontend
- No build step — edit JSX files and reload the browser
- `window.api` (defined in `api.js`) is the shared HTTP client
- `window.RS_API_BASE` defaults to `http://localhost:8080`
- CSS variables (colors, spacing, fonts) are defined in `styles.css` root block
- New components go in `frontend/components/`, exposed via `Object.assign(window, { MyComponent })`

---

## MCP server

`mcp_server/server.py` exposes the same research tools to Claude Code via FastMCP (JSON-RPC stdio). This lets Claude Code in this session call the same tools the web agent uses. Run it with:

```bash
python mcp_server/server.py
```

Config is in `opencode.json` at the project root.

---

## Database tables (SQLite)

| Table | Purpose |
|---|---|
| `papers` | Core paper records |
| `paper_summaries` | LLM-generated summaries |
| `paper_entities` | Extracted entities (people, projects, methods, etc.) |
| `entity_relationships` | Typed relationships between entities |
| `citations` | Citation edges between papers |
| `users` | User accounts |
| `collections` | Named paper collections |
| `watches` | Saved search watches |
| `chats` | Chat session history |
| `pins` | Pinned papers per user |
| `notes` | Research notes per paper |
| `web_paper_links` | Links between `web:` papers and corpus papers |
| `ingest_runs` | Ingestion run history (interests, found count, timestamps) |

---

## Data directories (git-ignored)

| Path | Contents |
|---|---|
| `data/papers.db` | SQLite database |
| `data/chroma/` | ChromaDB vector embeddings |
| `data/ocr/` | Cached plain-text from PDFs and OCR |
| `data/pdfs/` | Downloaded PDF files |
| `data/web_screenshots/` | JPEG viewport screenshots from web ingestion |
| `workspace/` | Agent-generated HTML dashboards |
