/**
 * Meridian API client — typed wrapper around the FastAPI backend.
 *
 * `API_BASE` is an empty string so all fetch() calls use the same origin.
 * In development (Vite dev server on :5173) the vite.config.ts proxy
 * forwards all API paths to FastAPI on :8080.  In production FastAPI
 * serves both the built frontend and the API from the same port.
 */

import type {
  AgentStreamCallbacks,
  BatchStatus,
  Chat,
  ChatStreamCallbacks,
  Citation,
  CitationGraph,
  Collection,
  Health,
  Note,
  Paper,
  PaperFetchOptions,
  ProcessingItem,
  StreamHandle,
  Traversal,
} from "./types";

// Allow the host app or .env to override the API base at runtime.
export const API_BASE: string =
  (window as Window & { RS_API_BASE?: string }).RS_API_BASE ??
  import.meta.env.VITE_API_BASE ??
  "";

// ── Paper list ────────────────────────────────────────────────────────────────

async function fetchPapers(opts: PaperFetchOptions = {}): Promise<Paper[]> {
  const {
    q = "",
    topics = [],
    sources = [],
    status = [],
    pinned = null,
    since_days = null,
    sort = "date",
    limit = 500,
    offset = 0,
  } = opts;
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  topics.forEach((t) => params.append("topics", t));
  sources.forEach((s) => params.append("sources", s));
  status.forEach((s) => params.append("status", s));
  if (pinned !== null) params.set("pinned", String(pinned));
  if (since_days !== null) params.set("since_days", String(since_days));
  params.set("sort", sort);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  const res = await fetch(`${API_BASE}/papers/queue?${params}`);
  if (!res.ok) throw new Error(`fetchPapers: ${res.status}`);
  return res.json() as Promise<Paper[]>;
}

async function fetchPaper(id: string): Promise<Paper> {
  const res = await fetch(`${API_BASE}/papers/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`fetchPaper: ${res.status}`);
  return res.json() as Promise<Paper>;
}

async function fetchTrace(id: string): Promise<unknown[]> {
  const res = await fetch(`${API_BASE}/papers/${encodeURIComponent(id)}/trace`);
  if (!res.ok) return [];
  return res.json() as Promise<unknown[]>;
}

async function fetchCitations(id: string): Promise<CitationGraph> {
  const res = await fetch(`${API_BASE}/papers/${encodeURIComponent(id)}/citations`);
  if (!res.ok) return { nodes: [], edges: [] };
  return res.json() as Promise<CitationGraph>;
}

function citationEdges(graph: CitationGraph): Citation[] {
  return (graph.edges ?? []).map((e) => ({ from: e.from_id, to: e.to_id }));
}

async function fetchVelocity(id: string): Promise<unknown> {
  const res = await fetch(`${API_BASE}/papers/${encodeURIComponent(id)}/velocity`);
  if (!res.ok) return null;
  return res.json();
}

// ── System ────────────────────────────────────────────────────────────────────

async function fetchHealth(): Promise<Health | null> {
  const res = await fetch(`${API_BASE}/system/health`);
  if (!res.ok) return null;
  return res.json() as Promise<Health>;
}

async function fetchTokenUsage(): Promise<unknown> {
  const r = await fetch(`${API_BASE}/system/token-usage`);
  return r.ok ? (r.json() as Promise<unknown>) : null;
}

async function recordPaperView(paper_id: string, viewer = "user"): Promise<void> {
  fetch(`${API_BASE}/system/paper-views`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paper_id, viewer }),
  }).catch(() => {});
}

async function fetchPaperViews(): Promise<unknown> {
  const r = await fetch(`${API_BASE}/system/paper-views`);
  return r.ok ? (r.json() as Promise<unknown>) : null;
}

async function fetchTaxonomy(): Promise<unknown[]> {
  const res = await fetch(`${API_BASE}/taxonomy/lanes`);
  if (!res.ok) return [];
  return res.json() as Promise<unknown[]>;
}

// ── Pin ───────────────────────────────────────────────────────────────────────

async function togglePin(id: string): Promise<unknown> {
  const res = await fetch(`${API_BASE}/papers/${encodeURIComponent(id)}/pin`, { method: "POST" });
  if (!res.ok) throw new Error(`togglePin: ${res.status}`);
  return res.json();
}

async function pinPaper(id: string): Promise<Response> {
  return fetch(`${API_BASE}/users/me/pins/${encodeURIComponent(id)}`, { method: "POST" });
}

async function unpinPaper(id: string): Promise<Response> {
  return fetch(`${API_BASE}/users/me/pins/${encodeURIComponent(id)}`, { method: "DELETE" });
}

// ── Ingest ────────────────────────────────────────────────────────────────────

async function ingestById(arxiv_id: string): Promise<{ paper: Paper }> {
  const res = await fetch(`${API_BASE}/ingest/paper`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ arxiv_id }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({})) as { detail?: string };
    throw new Error(err.detail ?? `ingestById: ${res.status}`);
  }
  return res.json() as Promise<{ paper: Paper }>;
}

interface IngestOptions {
  interests: string[];
  sources: string[];
  window_days?: number;
  date_from?: string | null;
  date_to?: string | null;
  save_as_watch?: boolean;
  arxiv_categories?: string[] | null;
  biorxiv_categories?: string[] | null;
}

async function startIngest(opts: IngestOptions): Promise<{ job_id: string }> {
  const { interests, sources, window_days = 14, date_from = null, date_to = null,
          save_as_watch = false, arxiv_categories = null, biorxiv_categories = null } = opts;
  const body: Record<string, unknown> = { interests, sources, window_days, save_as_watch };
  if (date_from) body.date_from = date_from;
  if (date_to) body.date_to = date_to;
  if (arxiv_categories?.length) body.arxiv_categories = arxiv_categories;
  if (biorxiv_categories?.length) body.biorxiv_categories = biorxiv_categories;
  const res = await fetch(`${API_BASE}/ingest/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`startIngest: ${res.status}`);
  return res.json() as Promise<{ job_id: string }>;
}

interface IngestSocketCallbacks {
  onPhase?: (frame: unknown) => void;
  onPaper?: (partial: Paper) => void;
  onDone?: (frame: { found: number; scanned: number; duration_ms: number }) => void;
  onError?: (msg: string) => void;
}

function openIngestSocket(job_id: string, cbs: IngestSocketCallbacks = {}): WebSocket {
  const wsUrl = API_BASE.replace(/^http/, "ws") + `/ws/ingest/${job_id}`;
  const ws = new WebSocket(wsUrl);
  ws.onmessage = (ev) => {
    try {
      const frame = JSON.parse(ev.data as string) as {
        type: string; partial?: Paper; found?: number; scanned?: number;
        duration_ms?: number; message?: string;
      };
      if (frame.type === "phase" && cbs.onPhase) cbs.onPhase(frame);
      if (frame.type === "paper" && cbs.onPaper && frame.partial) cbs.onPaper(frame.partial);
      if (frame.type === "done" && cbs.onDone) {
        cbs.onDone({ found: frame.found ?? 0, scanned: frame.scanned ?? 0, duration_ms: frame.duration_ms ?? 0 });
        ws.close();
      }
      if (frame.type === "error" && cbs.onError) { cbs.onError(frame.message ?? "error"); ws.close(); }
    } catch { /* malformed frame */ }
  };
  ws.onerror = () => cbs.onError?.("WebSocket error");
  return ws;
}

async function fetchIngestSummary(): Promise<unknown> {
  const res = await fetch(`${API_BASE}/ingest/summary`);
  return res.ok ? res.json() : null;
}

async function fetchIngestPlan(
  interests: string[], sources: string[], window_days = 14
): Promise<unknown> {
  const res = await fetch(`${API_BASE}/ingest/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ interests, sources, window_days }),
  });
  return res.ok ? res.json() : null;
}

// ── User / library ────────────────────────────────────────────────────────────

async function fetchConfig(): Promise<unknown> {
  const r = await fetch(`${API_BASE}/system/config`);
  return r.ok ? r.json() : null;
}

async function saveConfig(body: Record<string, unknown>): Promise<unknown> {
  const r = await fetch(`${API_BASE}/system/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.ok ? r.json() : null;
}

async function fetchMe(): Promise<unknown> {
  const r = await fetch(`${API_BASE}/users/me`);
  return r.ok ? r.json() : null;
}

async function fetchLibrarySummary(): Promise<unknown> {
  const r = await fetch(`${API_BASE}/users/me/library/summary`);
  return r.ok ? r.json() : null;
}

async function fetchPins(): Promise<Paper[]> {
  const r = await fetch(`${API_BASE}/users/me/pins`);
  return r.ok ? (r.json() as Promise<Paper[]>) : [];
}

async function fetchCollections(): Promise<Collection[]> {
  const r = await fetch(`${API_BASE}/users/me/collections`);
  return r.ok ? (r.json() as Promise<Collection[]>) : [];
}

async function deleteCollection(colId: string, deletePapers = false): Promise<boolean> {
  const r = await fetch(
    `${API_BASE}/users/me/collections/${colId}?delete_papers=${deletePapers}`,
    { method: "DELETE" }
  );
  return r.ok || r.status === 204;
}

async function deleteIngest(ingestId: string, deletePapers = false): Promise<boolean> {
  const r = await fetch(
    `${API_BASE}/users/me/ingests/${ingestId}?delete_papers=${deletePapers}`,
    { method: "DELETE" }
  );
  return r.ok || r.status === 204;
}

async function fetchNotes(paperId: string): Promise<Note[]> {
  const r = await fetch(`${API_BASE}/users/me/papers/${encodeURIComponent(paperId)}/notes`);
  return r.ok ? (r.json() as Promise<Note[]>) : [];
}

async function createNote(paperId: string, content: string): Promise<Note> {
  const r = await fetch(
    `${API_BASE}/users/me/papers/${encodeURIComponent(paperId)}/notes`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, source: "user" }),
    }
  );
  if (!r.ok) throw new Error(`createNote: ${r.status}`);
  return r.json() as Promise<Note>;
}

async function updateNote(paperId: string, noteId: string, content: string): Promise<Note> {
  const r = await fetch(
    `${API_BASE}/users/me/papers/${encodeURIComponent(paperId)}/notes/${noteId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }
  );
  if (!r.ok) throw new Error(`updateNote: ${r.status}`);
  return r.json() as Promise<Note>;
}

async function deleteNote(paperId: string, noteId: string): Promise<boolean> {
  const r = await fetch(
    `${API_BASE}/users/me/papers/${encodeURIComponent(paperId)}/notes/${noteId}`,
    { method: "DELETE" }
  );
  return r.ok || r.status === 204;
}

async function fetchNotebooks(): Promise<unknown[]> {
  const r = await fetch(`${API_BASE}/users/me/notebooks`);
  return r.ok ? (r.json() as Promise<unknown[]>) : [];
}

async function fetchChats(): Promise<Chat[]> {
  const r = await fetch(`${API_BASE}/users/me/chats`);
  return r.ok ? (r.json() as Promise<Chat[]>) : [];
}

async function fetchManuallyAdded(): Promise<unknown[]> {
  const r = await fetch(`${API_BASE}/users/me/manually-added`);
  return r.ok ? (r.json() as Promise<unknown[]>) : [];
}

async function fetchWatches(): Promise<unknown[]> {
  const r = await fetch(`${API_BASE}/watches`);
  return r.ok ? (r.json() as Promise<unknown[]>) : [];
}

async function fetchModelCatalog(): Promise<unknown[]> {
  const r = await fetch(`${API_BASE}/models/catalog`);
  return r.ok ? (r.json() as Promise<unknown[]>) : [];
}

async function fetchBatchStatus(): Promise<BatchStatus | null> {
  const r = await fetch(`${API_BASE}/batch/status`);
  return r.ok ? (r.json() as Promise<BatchStatus>) : null;
}

async function startBatch(
  action: string,
  filter = "all",
  paperIds: string[] | null = null
): Promise<unknown> {
  const body: Record<string, unknown> = { action, filter };
  if (paperIds) body.paper_ids = paperIds;
  const r = await fetch(`${API_BASE}/batch/process`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.ok ? r.json() : null;
}

// ── Reader ────────────────────────────────────────────────────────────────────

async function triggerSummarise(id: string): Promise<unknown> {
  const r = await fetch(`${API_BASE}/papers/${encodeURIComponent(id)}/summarise`, { method: "POST" });
  return r.ok ? r.json() : null;
}

async function fetchReader(id: string): Promise<unknown> {
  const res = await fetch(`${API_BASE}/papers/${encodeURIComponent(id)}/reader`);
  return res.ok ? res.json() : null;
}

async function fetchFulltext(id: string): Promise<unknown> {
  const res = await fetch(`${API_BASE}/papers/${encodeURIComponent(id)}/fulltext`);
  return res.ok ? res.json() : null;
}

async function fetchOcrProgress(id: string): Promise<unknown> {
  const res = await fetch(`${API_BASE}/papers/${encodeURIComponent(id)}/ocr/progress`);
  return res.ok ? res.json() : null;
}

async function fetchSummariseProgress(id: string): Promise<unknown> {
  const res = await fetch(`${API_BASE}/papers/${encodeURIComponent(id)}/summarise/progress`);
  return res.ok ? res.json() : null;
}

async function fetchNeighbors(id: string, k = 6): Promise<unknown> {
  const res = await fetch(`${API_BASE}/papers/${encodeURIComponent(id)}/neighbors?k=${k}`);
  return res.ok ? res.json() : null;
}

// ── Chat ──────────────────────────────────────────────────────────────────────

async function newChat(paperId: string | null = null): Promise<{ id: string }> {
  const res = await fetch(`${API_BASE}/chats`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(paperId ? { paper_id: paperId } : {}),
  });
  return res.ok ? (res.json() as Promise<{ id: string }>) : { id: crypto.randomUUID() };
}

async function fetchPaperChats(paperId: string | null): Promise<Chat[]> {
  const params = paperId ? `?paper_id=${encodeURIComponent(paperId)}` : "";
  const r = await fetch(`${API_BASE}/users/me/chats${params}`);
  return r.ok ? (r.json() as Promise<Chat[]>) : [];
}

async function deleteChat(chatId: string): Promise<Response> {
  return fetch(`${API_BASE}/users/me/chats/${encodeURIComponent(chatId)}`, { method: "DELETE" });
}

async function fetchChatMessages(chatId: string): Promise<unknown[]> {
  const r = await fetch(`${API_BASE}/chats/${encodeURIComponent(chatId)}/messages`);
  return r.ok ? (r.json() as Promise<unknown[]>) : [];
}

async function sendChatMessage(
  chatId: string, text: string, paperId: string | null = null
): Promise<unknown> {
  const res = await fetch(`${API_BASE}/chats/${encodeURIComponent(chatId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, paper_id: paperId }),
  });
  return res.ok ? res.json() : null;
}

function _parseSSE(
  res: Response,
  handlers: AgentStreamCallbacks & {
    onToolCall?: AgentStreamCallbacks["onToolCall"];
    onToolResult?: AgentStreamCallbacks["onToolResult"];
  }
): void {
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  void (async () => {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6).trim();
        if (!payload) continue;
        try {
          const ev = JSON.parse(payload) as {
            type: string; delta?: string; message?: string;
            id?: string; tool?: string; input?: unknown; content?: string; agent?: string;
          };
          if      (ev.type === "thinking"    && handlers.onThinking)    handlers.onThinking(ev.delta ?? "");
          else if (ev.type === "content"     && handlers.onContent)     handlers.onContent(ev.delta ?? "");
          else if (ev.type === "done"        && handlers.onDone)        handlers.onDone();
          else if (ev.type === "error"       && handlers.onError)       handlers.onError(ev.message ?? ev.delta ?? "");
          else if (ev.type === "tool_call"   && handlers.onToolCall)
            handlers.onToolCall({ id: ev.id!, tool: ev.tool!, input: ev.input, agent: ev.agent ?? "Meridian" });
          else if (ev.type === "tool_result" && handlers.onToolResult)
            handlers.onToolResult({ id: ev.id!, tool: ev.tool!, content: ev.content ?? "", agent: ev.agent ?? "Meridian" });
        } catch { /* skip malformed event */ }
      }
    }
  })();
}

function streamChatMessage(
  chatId: string,
  text: string,
  paperId: string | null = null,
  mode = "paper",
  cbs: ChatStreamCallbacks = {},
  images: string[] = [],
): StreamHandle {
  const controller = new AbortController();
  fetch(`${API_BASE}/chats/${encodeURIComponent(chatId)}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, paper_id: paperId, mode, images: images.length ? images : undefined }),
    signal: controller.signal,
  }).then((res) => {
    if (!res.ok) { cbs.onError?.(`HTTP ${res.status}`); return; }
    _parseSSE(res, cbs);
  }).catch((err: Error) => {
    if (err.name !== "AbortError") cbs.onError?.(String(err));
  });
  return { abort: () => controller.abort() };
}

function streamAgentMessage(
  chatId: string,
  text: string,
  paperId: string | null = null,
  thinking = true,
  cbs: AgentStreamCallbacks = {},
  images: string[] = [],
): StreamHandle {
  const controller = new AbortController();
  fetch(`${API_BASE}/chats/${encodeURIComponent(chatId)}/messages/agent-stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, paper_id: paperId, thinking, images: images.length ? images : undefined }),
    signal: controller.signal,
  }).then((res) => {
    if (!res.ok) { cbs.onError?.(`HTTP ${res.status}`); return; }
    _parseSSE(res, cbs);
  }).catch((err: Error) => {
    if (err.name !== "AbortError") cbs.onError?.(String(err));
  });
  return { abort: () => controller.abort() };
}

// ── Export / PDF ──────────────────────────────────────────────────────────────

function exportUrl(id: string, fmt: string): string {
  return `${API_BASE}/papers/${encodeURIComponent(id)}/export.${fmt}`;
}

function pdfUrl(id: string): string {
  return `${API_BASE}/papers/${encodeURIComponent(id)}/pdf.pdf`;
}

// ── Graph ─────────────────────────────────────────────────────────────────────

async function fetchGraphData(
  limit = 500,
  semantic = false,
  simThreshold = 0.75,
  layout = "force",
  authorEdges = false,
  llmEdges = false,
): Promise<unknown> {
  const params = new URLSearchParams({
    limit: String(limit),
    semantic: String(semantic),
    sim_threshold: String(simThreshold),
    layout,
    author_edges: String(authorEdges),
    llm_edges: String(llmEdges),
  });
  const r = await fetch(`${API_BASE}/papers/graph?${params}`);
  return r.ok
    ? r.json()
    : { nodes: [], edges: [], semantic_edges: [], author_edges: [], llm_edges: [] };
}

async function classifyEdges(neighbors = 3, allSources = false): Promise<unknown> {
  const r = await fetch(
    `${API_BASE}/papers/graph/edges/classify?neighbors=${neighbors}&all_sources=${allSources}`,
    { method: "POST" }
  );
  return r.ok ? r.json() : null;
}

async function stopClassifyEdges(): Promise<unknown> {
  const r = await fetch(`${API_BASE}/papers/graph/edges/classify/stop`, { method: "POST" });
  return r.ok ? r.json() : null;
}

async function fetchClassifyStatus(): Promise<unknown> {
  const r = await fetch(`${API_BASE}/papers/graph/edges/classify/status`);
  return r.ok ? r.json() : null;
}

async function fetchEdges(source: string | null = null, edgeType: string | null = null): Promise<unknown[]> {
  const params = new URLSearchParams();
  if (source) params.set("source", source);
  if (edgeType) params.set("edge_type", edgeType);
  const r = await fetch(`${API_BASE}/papers/graph/edges?${params}`);
  return r.ok ? (r.json() as Promise<unknown[]>) : [];
}

async function deleteEdges(source: string | null = null): Promise<unknown> {
  const params = new URLSearchParams();
  if (source) params.set("source", source);
  const r = await fetch(`${API_BASE}/papers/graph/edges?${params}`, { method: "DELETE" });
  return r.ok ? r.json() : null;
}

// ── Embeddings ────────────────────────────────────────────────────────────────

async function fetchEmbedStatus(): Promise<{ embedded: number; total: number }> {
  const r = await fetch(`${API_BASE}/papers/embed/status`);
  return r.ok ? (r.json() as Promise<{ embedded: number; total: number }>) : { embedded: 0, total: 0 };
}

async function embedPaper(id: string): Promise<Response> {
  return fetch(`${API_BASE}/papers/${encodeURIComponent(id)}/embed`, { method: "POST" });
}

async function embedBatch(): Promise<Response> {
  return fetch(`${API_BASE}/papers/embed/batch`, { method: "POST" });
}

// ── Processing ────────────────────────────────────────────────────────────────

async function fetchProcessingStatus(): Promise<{ items: ProcessingItem[] }> {
  const r = await fetch(`${API_BASE}/processing/status`);
  return r.ok ? (r.json() as Promise<{ items: ProcessingItem[] }>) : { items: [] };
}

// ── Discover ──────────────────────────────────────────────────────────────────

interface DiscoverCallbacks {
  onCandidates?: (papers: Paper[]) => void;
  onContent?: (delta: string) => void;
  onDone?: () => void;
  onError?: (msg: string) => void;
}

function streamDiscover(paperId: string, n = 10, cbs: DiscoverCallbacks = {}): AbortController {
  const controller = new AbortController();
  fetch(`${API_BASE}/papers/${encodeURIComponent(paperId)}/discover`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ n }),
    signal: controller.signal,
  }).then(async (res) => {
    if (!res.ok) { cbs.onError?.(`HTTP ${res.status}`); return; }
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() ?? "";
      for (const part of parts) {
        if (!part.startsWith("data:")) continue;
        try {
          const ev = JSON.parse(part.slice(5).trim()) as {
            type: string; papers?: Paper[]; delta?: string;
          };
          if (ev.type === "candidates" && cbs.onCandidates) cbs.onCandidates(ev.papers ?? []);
          if (ev.type === "content"    && cbs.onContent)    cbs.onContent(ev.delta ?? "");
          if (ev.type === "done"       && cbs.onDone)       cbs.onDone();
          if (ev.type === "error"      && cbs.onError)      cbs.onError(ev.delta ?? "");
        } catch { /* skip */ }
      }
    }
  }).catch((e: Error) => { if (e.name !== "AbortError") cbs.onError?.(String(e)); });
  return controller;
}

// ── Prompts ───────────────────────────────────────────────────────────────────

async function fetchPrompts(): Promise<unknown[]> {
  const r = await fetch(`${API_BASE}/prompts`);
  return r.ok ? (r.json() as Promise<unknown[]>) : [];
}

async function fetchPrompt(name: string): Promise<unknown> {
  const r = await fetch(`${API_BASE}/prompts/${name}`);
  return r.ok ? r.json() : null;
}

async function savePrompt(name: string, raw: string): Promise<unknown> {
  const r = await fetch(`${API_BASE}/prompts/${name}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw }),
  });
  return r.ok ? r.json() : null;
}

async function createSkill(name: string, raw: string): Promise<unknown> {
  const r = await fetch(`${API_BASE}/prompts/skills`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, raw }),
  });
  if (r.status === 409) throw new Error("A skill with that name already exists.");
  if (!r.ok) throw new Error(`Create failed: ${r.status}`);
  return r.json();
}

async function deletePrompt(name: string): Promise<void> {
  const r = await fetch(`${API_BASE}/prompts/${name}`, { method: "DELETE" });
  if (!r.ok && r.status !== 204) throw new Error(`Delete failed: ${r.status}`);
}

// ── Workspace / misc ──────────────────────────────────────────────────────────

async function fetchWorkspaceFiles(paperId: string | null): Promise<unknown[]> {
  const url = paperId
    ? `${API_BASE}/workspace/files?paper_id=${encodeURIComponent(paperId)}`
    : `${API_BASE}/workspace/files`;
  const r = await fetch(url);
  return r.ok ? (r.json() as Promise<unknown[]>) : [];
}

async function fetchManualPaperIds(): Promise<string[]> {
  const r = await fetch(`${API_BASE}/papers/manually-added`);
  return r.ok ? (r.json() as Promise<string[]>) : [];
}

async function fetchEntities(paperId: string): Promise<{ entities: unknown[]; relationships: unknown[] }> {
  const r = await fetch(`${API_BASE}/papers/${encodeURIComponent(paperId)}/entities`);
  return r.ok
    ? (r.json() as Promise<{ entities: unknown[]; relationships: unknown[] }>)
    : { entities: [], relationships: [] };
}

async function triggerEntityExtraction(paperId: string): Promise<unknown> {
  const r = await fetch(
    `${API_BASE}/papers/${encodeURIComponent(paperId)}/entities/extract`,
    { method: "POST" }
  );
  return r.json();
}

async function fetchLastTraversal(): Promise<Traversal | null> {
  const r = await fetch(`${API_BASE}/papers/traverse/last`);
  return r.ok ? (r.json() as Promise<Traversal>) : null;
}

async function fetchWebScreenshots(paperId: string | null): Promise<unknown[]> {
  if (!paperId) return [];
  const r = await fetch(`${API_BASE}/papers/${encodeURIComponent(paperId)}/screenshots`);
  return r.ok ? (r.json() as Promise<unknown[]>) : [];
}

async function fetchWebPages(paperId: string | null): Promise<unknown[]> {
  if (!paperId) return [];
  const r = await fetch(`${API_BASE}/papers/${encodeURIComponent(paperId)}/web-pages`);
  return r.ok ? (r.json() as Promise<unknown[]>) : [];
}

async function fetchTraversals(): Promise<Traversal[]> {
  const r = await fetch(`${API_BASE}/papers/traversals`);
  return r.ok ? (r.json() as Promise<Traversal[]>) : [];
}

async function clearTraversals(): Promise<unknown> {
  const r = await fetch(`${API_BASE}/papers/traversals`, { method: "DELETE" });
  return r.ok ? r.json() : null;
}

async function deleteTraversal(idx: number): Promise<unknown> {
  const r = await fetch(`${API_BASE}/papers/traversals/${idx}`, { method: "DELETE" });
  return r.ok ? r.json() : null;
}

async function clearLastTraversal(): Promise<unknown> {
  const r = await fetch(`${API_BASE}/papers/traverse/last`, { method: "DELETE" });
  return r.ok ? r.json() : null;
}

// ── Boot ──────────────────────────────────────────────────────────────────────

async function boot(): Promise<void> {
  try {
    const health = await fetchHealth();
    const PAGE = 500;
    const all: Paper[] = [];
    let offset = 0;
    while (true) {
      const page = await fetchPapers({ limit: PAGE, offset });
      all.push(...page);
      if (page.length < PAGE) break;
      offset += PAGE;
    }
    document.dispatchEvent(
      new CustomEvent("rs:loaded", { detail: { papers: all, health } })
    );
  } catch (err) {
    console.warn("ResearchStation API unreachable — UI will start empty.", err);
    document.dispatchEvent(
      new CustomEvent("rs:loaded", { detail: { papers: [], health: null } })
    );
  }
}

// ── Export ────────────────────────────────────────────────────────────────────

export const api = {
  fetchPapers,
  fetchPaper,
  fetchTrace,
  fetchCitations,
  citationEdges,
  fetchVelocity,
  fetchHealth,
  fetchTokenUsage,
  recordPaperView,
  fetchPaperViews,
  fetchTaxonomy,
  togglePin,
  pinPaper,
  unpinPaper,
  ingestById,
  startIngest,
  openIngestSocket,
  fetchIngestSummary,
  fetchIngestPlan,
  fetchConfig,
  saveConfig,
  fetchMe,
  fetchLibrarySummary,
  fetchPins,
  fetchCollections,
  deleteCollection,
  deleteIngest,
  fetchNotes,
  createNote,
  updateNote,
  deleteNote,
  fetchNotebooks,
  fetchChats,
  fetchManuallyAdded,
  fetchWatches,
  fetchModelCatalog,
  fetchBatchStatus,
  startBatch,
  triggerSummarise,
  fetchReader,
  fetchFulltext,
  fetchOcrProgress,
  fetchSummariseProgress,
  fetchNeighbors,
  newChat,
  fetchPaperChats,
  deleteChat,
  fetchChatMessages,
  sendChatMessage,
  streamChatMessage,
  streamAgentMessage,
  exportUrl,
  pdfUrl,
  fetchGraphData,
  classifyEdges,
  stopClassifyEdges,
  fetchClassifyStatus,
  fetchEdges,
  deleteEdges,
  fetchEmbedStatus,
  embedPaper,
  embedBatch,
  fetchProcessingStatus,
  streamDiscover,
  fetchPrompts,
  fetchPrompt,
  savePrompt,
  createSkill,
  deletePrompt,
  fetchWorkspaceFiles,
  fetchManualPaperIds,
  fetchEntities,
  triggerEntityExtraction,
  fetchLastTraversal,
  fetchWebScreenshots,
  fetchWebPages,
  fetchTraversals,
  clearTraversals,
  deleteTraversal,
  clearLastTraversal,
  boot,
};
