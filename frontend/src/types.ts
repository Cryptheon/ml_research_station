/** Domain types shared across the Meridian frontend. */

// ── Papers ────────────────────────────────────────────────────────────────────

export interface PaperScores {
  relevance: number;
  novelty: number;
  velocity: number;
}

export interface Paper {
  id: string;
  title: string;
  authors: string[];
  venue: string;
  date: string;
  source: string;
  topics: string[];
  scores: PaperScores;
  abstract: string;
  pinned: boolean;
  status: string;
  cache_flags: number;
  velocity_12w: number[];
  created_at?: string;
  is_downloaded?: boolean;
  pdf_url?: string;
}

export interface Citation {
  from: string;
  to: string;
}

export interface CitationGraph {
  nodes: Array<{ id: string; title: string }>;
  edges: Array<{ from_id: string; to_id: string }>;
}

// ── System ────────────────────────────────────────────────────────────────────

export interface Health {
  paper_count: number;
  llm_provider: string;
  llm_model: string;
}

export interface ProcessingItem {
  paper_id: string;
  action: string;
  pages_done: number;
  pages_total: number;
  done: number;
  total: number;
}

export interface BatchStatus {
  running: boolean;
  action: string;
  done: number;
  total: number;
  errors: number;
  current?: string;
}

// ── User / library ────────────────────────────────────────────────────────────

export interface Note {
  id: string;
  paper_id: string;
  content: string;
  source: string;
  created_at: string;
}

export interface Collection {
  id: string;
  name: string;
  paper_ids: string[];
  created_at: string;
}

export interface Chat {
  id: string;
  paper_id: string | null;
  created_at: string;
  updated_at: string;
  title?: string;
}

export interface IngestRun {
  id: string;
  interests: string[];
  found: number;
  scanned: number;
  ran_at: string;
  duration_seconds?: number;
  paper_ids?: string[];
}

// ── Graph / traversal ─────────────────────────────────────────────────────────

export interface TraversalNode {
  id: string;
  title: string;
  depth: number;
}

export interface Traversal {
  root_id: string;
  nodes: TraversalNode[];
  edges: Array<{ from: string; to: string }>;
}

// ── Streaming callbacks ───────────────────────────────────────────────────────

export interface ChatStreamCallbacks {
  onThinking?: (delta: string) => void;
  onContent?: (delta: string) => void;
  onDone?: () => void;
  onError?: (msg: string) => void;
}

export interface AgentStreamCallbacks extends ChatStreamCallbacks {
  onToolCall?: (call: { id: string; tool: string; input: unknown; agent: string }) => void;
  onToolResult?: (result: { id: string; tool: string; content: string; agent: string }) => void;
}

export interface StreamHandle {
  abort: () => void;
}

// ── Misc ──────────────────────────────────────────────────────────────────────

export interface PaperFetchOptions {
  q?: string;
  topics?: string[];
  sources?: string[];
  status?: string[];
  pinned?: boolean | null;
  since_days?: number | null;
  sort?: string;
  limit?: number;
  offset?: number;
}
