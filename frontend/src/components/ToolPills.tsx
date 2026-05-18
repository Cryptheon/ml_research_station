import { useState, useEffect, useRef } from "react";
import { api } from "../api";
import type { Paper } from "../types";

declare global {
  interface Window {
    Prism?: { highlightElement: (el: Element) => void };
  }
}

import { marked } from "marked";
import renderMathInElement from "katex/contrib/auto-render";

const _mdKatexOpts = {
  delimiters: [
    { left: "$$", right: "$$", display: true },
    { left: "$",  right: "$",  display: false },
  ],
  throwOnError: false,
};

// ── ChatMd ────────────────────────────────────────────────────────────────────

export function ChatMd({ text, streaming }: { text: string; streaming?: boolean }) {
  const elRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!streaming && elRef.current) {
      renderMathInElement(elRef.current, _mdKatexOpts);
    }
  }, [text, streaming]);
  if (!text) return null;
  const html = marked.parse(text) as string;
  return (
    <span ref={elRef}>
      <span className="md-body chat-md" dangerouslySetInnerHTML={{ __html: html }} />
      {streaming && <span style={{ opacity: 0.4, marginLeft: 1 }}>▌</span>}
    </span>
  );
}

// ── ToolCall (legacy) ─────────────────────────────────────────────────────────

export function ToolCall({ tool, arg, result }: { tool: string; arg?: string; result?: string }) {
  return (
    <div className="tc">
      <div className="tc-head">
        <span className="tc-dot" />
        <span className="tc-name">{tool}</span>
        <span className="tc-arg">{arg}</span>
      </div>
      {result && <div className="tc-result">{result}</div>}
    </div>
  );
}

// ── SummaryProgressBar ────────────────────────────────────────────────────────

interface SummariseProgress {
  stage?: string;
  chunks_done?: number;
  chunks_total?: number;
}

function SummaryProgressBar({ paperId }: { paperId?: string }) {
  const [prog, setProg] = useState<SummariseProgress | null>(null);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const t0 = Date.now();
    const tick = setInterval(() => {
      setElapsed(Math.floor((Date.now() - t0) / 1000));
      if (paperId && api.fetchSummariseProgress) {
        api.fetchSummariseProgress(paperId).then(raw => setProg(raw as SummariseProgress | null)).catch(() => {});
      }
    }, 2000);
    return () => clearInterval(tick);
  }, [paperId]);

  const isMapReduce = prog?.stage === "map_reduce" && (prog?.chunks_total ?? 0) > 0;
  const fakeWidth = Math.min(90, (elapsed / 120) * 100);

  return (
    <div style={{ marginTop: 6, minWidth: 200 }}>
      <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 3 }}>
        {isMapReduce
          ? `Map-reduce · chunk ${prog!.chunks_done} / ${prog!.chunks_total}`
          : `Summarising with LLM… (${elapsed}s)`}
      </div>
      <div style={{ height: 4, background: "var(--bg-3)", borderRadius: 2, overflow: "hidden" }}>
        {isMapReduce ? (
          <div style={{
            height: "100%", borderRadius: 2, background: "var(--rust)",
            width: `${((prog!.chunks_done ?? 0) / (prog!.chunks_total ?? 1)) * 100}%`,
            transition: "width 0.4s ease",
          }} />
        ) : (
          <div style={{
            height: "100%", borderRadius: 2, background: "var(--rust)",
            width: `${fakeWidth}%`,
            transition: "width 2s linear",
          }} />
        )}
      </div>
      {isMapReduce && (
        <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 2 }}>
          Condensing full text before final synthesis…
        </div>
      )}
    </div>
  );
}

// ── PythonBlock ───────────────────────────────────────────────────────────────

function PythonBlock({ code }: { code: string }) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (ref.current && window.Prism) window.Prism.highlightElement(ref.current);
  }, [code]);
  return (
    <pre className="tool-pill-code language-python" style={{ margin: 0 }}>
      <code ref={ref} className="language-python">{code}</code>
    </pre>
  );
}

// ── ToolCallPill ──────────────────────────────────────────────────────────────

const TOOL_ICONS: Record<string, string> = {
  search_papers: "◎", semantic_search: "◎", find_similar_papers: "◎", list_papers: "≡",
  get_paper: "◉", summarize_paper: "∑", ocr_paper: "⊡", embed_paper: "⊛",
  extract_pdf_text: "⊟", extract_entities: "⋯", get_entities: "⋯",
  rag_query: "≋", graph_traverse: "◈", query_database: "⊜",
  ingest_papers: "⊕", ingest_wikipedia_article: "⊕", ingest_webpage: "⊕",
  execute_python: "⌬", create_dashboard: "⊞", add_note: "◇", list_workspace: "≡",
};

function fmtArgPreview(_tool: string, input: Record<string, unknown> | null): string {
  if (!input || typeof input !== "object") return "";
  const entries = Object.entries(input);
  if (entries.length === 0) return "";
  const first = entries.slice(0, 2).map(([k, v]) => {
    let val = typeof v === "string" ? v : JSON.stringify(v);
    if (val.length > 40) val = val.slice(0, 40) + "…";
    return `${k}: ${val}`;
  });
  return first.join("  ·  ");
}

function fmtResultPreview(result: string): string {
  if (!result) return "";
  const s = result.replace(/\n+/g, " ").trim();
  return s.length > 100 ? s.slice(0, 100) + "…" : s;
}

export interface ToolEntry {
  id: string;
  tool: string;
  input: Record<string, unknown> | null;
  result?: string;
  streaming?: boolean;
  type?: string;
  agent?: string;
  children?: ToolEntry[];
}

interface ToolCallPillProps extends ToolEntry {
  nested?: boolean;
}

export function ToolCallPill({ id: _id, tool, input, result, streaming, nested }: ToolCallPillProps) {
  const [open, setOpen] = useState(false);
  const isWaiting = tool === "summarize_paper" && streaming && !result;
  const isPython = tool === "execute_python";
  const icon = TOOL_ICONS[tool] || "⚙";
  const isDone = !!result;
  const isRunning = streaming && !isDone;

  const argPreview = fmtArgPreview(tool, input);
  const resultPreview = isDone && !open ? fmtResultPreview(result!) : "";

  const renderResult = (raw: string) => {
    const truncated = raw.length > 3000 ? raw.slice(0, 3000) + "\n…[truncated]" : raw;
    if (isPython && truncated.includes("[stderr]:")) {
      const idx = truncated.indexOf("[stderr]:");
      const stdout = truncated.slice(0, idx).trim();
      const stderr = truncated.slice(idx + "[stderr]:".length).trim();
      return (
        <>
          {stdout && <pre className="tp-result">{stdout}</pre>}
          <div className="tp-stderr-head">stderr</div>
          <pre className="tp-result tp-stderr">{stderr}</pre>
        </>
      );
    }
    return <pre className="tp-result">{truncated}</pre>;
  };

  return (
    <div className={["tp", isDone ? "tp-done" : isRunning ? "tp-running" : "", nested ? "tp-nested" : "", open ? "tp-open" : ""].filter(Boolean).join(" ")}>
      <div className="tp-head" onClick={() => setOpen(o => !o)}>
        <span className="tp-icon">{icon}</span>
        <span className="tp-name">{isPython ? "python" : tool.replace(/_/g, "_\u200B")}</span>
        {!open && argPreview && <span className="tp-args">{argPreview}</span>}
        {!open && resultPreview && !argPreview && <span className="tp-result-preview">{resultPreview}</span>}
        <span className="tp-spacer" />
        {isRunning && !isWaiting && <span className="tool-pill-spinner" />}
        {isDone && <span className="tp-ok">✓</span>}
        <span className="tp-chev">{open ? "▴" : "▾"}</span>
      </div>
      {isWaiting && (
        <div style={{ padding: "4px 12px 8px" }}>
          <SummaryProgressBar paperId={input?.paper_id as string | undefined} />
        </div>
      )}
      {open && (
        <div className="tp-body">
          {isPython && input?.code ? (
            <>
              <PythonBlock code={input.code as string} />
              {input.timeout && <div className="tp-py-meta">timeout {input.timeout as number}s</div>}
            </>
          ) : (
            input && Object.keys(input).length > 0 && (
              <div className="tp-section">
                <div className="tp-section-label">args</div>
                <pre className="tp-input">{JSON.stringify(input, null, 2)}</pre>
              </div>
            )
          )}
          {result && (
            <div className="tp-section">
              <div className="tp-section-label">result</div>
              {renderResult(result)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── groupConsecutiveTools ─────────────────────────────────────────────────────

export function groupConsecutiveTools(tools: ToolEntry[]): ToolEntry[][] {
  const groups: ToolEntry[][] = [];
  for (const t of tools) {
    const last = groups[groups.length - 1];
    if (last && last[0].tool === t.tool) {
      last.push(t);
    } else {
      groups.push([t]);
    }
  }
  return groups;
}

// ── ToolGroupPill ─────────────────────────────────────────────────────────────

export function ToolGroupPill({ tools }: { tools: ToolEntry[] }) {
  const [open, setOpen] = useState(false);
  const name = tools[0].tool;
  const count = tools.length;
  const allDone = tools.every(t => !!t.result);
  const anyPending = tools.some(t => !t.result);
  const icon = TOOL_ICONS[name] || "⚙";
  return (
    <div className={["tp tp-group tp-nested", allDone ? "tp-done" : anyPending ? "tp-running" : "", open ? "tp-open" : ""].filter(Boolean).join(" ")}>
      <div className="tp-head" onClick={() => setOpen(o => !o)}>
        <span className="tp-icon">{icon}</span>
        <span className="tp-name">{name.replace(/_/g, "_\u200B")}</span>
        <span className="tp-count">×{count}</span>
        <span className="tp-spacer" />
        {anyPending && <span className="tool-pill-spinner" />}
        {allDone && <span className="tp-ok">✓</span>}
        <span className="tp-chev">{open ? "▴" : "▾"}</span>
      </div>
      {open && (
        <div className="tp-body tp-group-body">
          {tools.map(t => <ToolCallPill key={t.id} {...t} nested />)}
        </div>
      )}
    </div>
  );
}

// ── SubAgentPill ──────────────────────────────────────────────────────────────

const SUB_AGENT_COLORS: Record<string, string> = {
  research_expert:   "#5a8af0",
  processing_expert: "#e07020",
  knowledge_expert:  "#4db6ac",
  analysis_expert:   "#a8c97a",
};

export function SubAgentPill({ id: _id, tool, input, result, streaming, children = [] }: ToolEntry) {
  const [open, setOpen] = useState(true);
  const color = SUB_AGENT_COLORS[tool] || "var(--ink-3)";
  const isRunning = streaming && !result;
  const label = tool.replace("_expert", "").replace(/_/g, " ");
  const childGroups = groupConsecutiveTools(children);
  const taskText = input && (typeof input.input === "string" ? input.input : null);

  return (
    <div className="sub-agent-pill" style={{ borderColor: color + "55" }}>
      <div className="sub-agent-pill-head" onClick={() => setOpen(o => !o)}>
        <span className="sub-agent-dot" style={{ background: color }} />
        <span className="sub-agent-name" style={{ color }}>{label}</span>
        {!open && taskText && (
          <span className="sub-agent-task-preview">{taskText.length > 80 ? taskText.slice(0, 80) + "…" : taskText}</span>
        )}
        {children.length > 0 && (
          <span className="sub-agent-badge">{children.length} tool{children.length !== 1 ? "s" : ""}</span>
        )}
        {isRunning && <span className="tool-pill-spinner" />}
        {result && <span className="tool-pill-ok">✓</span>}
        <span className="tool-pill-chevron">{open ? "▲" : "▼"}</span>
      </div>
      {open && (
        <div className="sub-agent-children sa-connector">
          {taskText && (
            <div className="sub-agent-task-block">
              <span className="sub-agent-task-label">task</span>
              <span className="sub-agent-task-text">{taskText}</span>
            </div>
          )}
          {children.length === 0 && isRunning && (
            <div style={{ padding: "4px 10px", fontSize: 11, color: "var(--ink-4)", fontStyle: "italic" }}>
              running…
            </div>
          )}
          {childGroups.map((g, i) =>
            g.length === 1
              ? <ToolCallPill key={g[0].id} {...g[0]} nested />
              : <ToolGroupPill key={`cg-${i}`} tools={g} />
          )}
        </div>
      )}
    </div>
  );
}

// ── AgentProcessPill ──────────────────────────────────────────────────────────

interface AgentProcessPillProps {
  thinking: string | null;
  tools: ToolEntry[];
  streaming?: boolean;
  onStop?: () => void;
}

export function AgentProcessPill({ thinking, tools, streaming, onStop }: AgentProcessPillProps) {
  const [open, setOpen] = useState(false);
  if (!thinking && tools.length === 0) return null;

  const subAgents = tools.filter(t => t.type === "sub_agent");
  const directTools = tools.filter(t => t.type !== "sub_agent");
  const toolCount = tools.length;
  const pendingCount = tools.filter(t => !t.result).length;
  const toolGroups = groupConsecutiveTools(directTools);

  let label: string;
  if (streaming) {
    label = thinking ? "Thinking…" : "Working…";
  } else {
    const parts: string[] = [];
    if (thinking) parts.push("Thought");
    if (toolCount) parts.push(`${toolCount} tool${toolCount !== 1 ? "s" : ""}`);
    label = parts.join(" · ") || "Done";
  }

  let preview = "";
  if (!open) {
    if (thinking) {
      const firstLine = thinking.replace(/\n+/g, " ").trim();
      preview = firstLine.length > 180 ? firstLine.slice(0, 180) + "…" : firstLine;
    } else if (toolCount) {
      const saNames = subAgents.map(sa => sa.tool.replace("_expert", ""));
      const dtNames = toolGroups.map(g => g.length > 1 ? `${g[0].tool} ×${g.length}` : g[0].tool);
      preview = [...saNames, ...dtNames].join(", ");
    }
  }

  const pillCls = ["proc-pill", open ? "open" : "", streaming ? "streaming" : ""].filter(Boolean).join(" ");

  return (
    <div className={pillCls}>
      <div className="proc-pill-head" onClick={() => setOpen(o => !o)}>
        <span className="proc-pill-icon">◈</span>
        <div className="proc-pill-center">
          <span className="proc-pill-label">{label}</span>
          {!open && preview && <span className="proc-pill-preview">{preview}</span>}
        </div>
        {streaming && pendingCount > 0 && <span className="tool-pill-spinner" />}
        {streaming && onStop && (
          <button
            onClick={e => { e.stopPropagation(); onStop(); }}
            title="Stop agent"
            style={{
              marginLeft: 6, padding: "1px 7px", fontSize: 10, fontWeight: 600,
              border: "1px solid var(--rust)", borderRadius: 3,
              background: "color-mix(in srgb, var(--rust) 10%, transparent)",
              color: "var(--rust)", cursor: "pointer", flexShrink: 0,
              lineHeight: 1.6,
            }}
          >
            ■ Stop
          </button>
        )}
        <span className="proc-pill-chevron">{open ? "▲" : "▼"}</span>
      </div>
      {open && (
        <div className="proc-pill-body">
          {thinking && <pre className="proc-pill-trace">{thinking}</pre>}
          {(subAgents.length > 0 || toolGroups.length > 0) && (
            <div className="proc-pill-tools">
              {subAgents.map(sa => <SubAgentPill key={sa.id} {...sa} />)}
              {toolGroups.map((g, i) =>
                g.length === 1
                  ? <ToolCallPill key={g[0].id} {...g[0]} nested />
                  : <ToolGroupPill key={`grp-${i}-${g[0].tool}`} tools={g} />
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── PaperRef ──────────────────────────────────────────────────────────────────

export function PaperRef({ p, onOpen }: { p: Paper; onOpen: (id: string) => void }) {
  return (
    <button className="pref" onClick={() => onOpen(p.id)}>
      <span className="pref-venue">{p.venue}</span>
      <span className="pref-title">{p.title.length > 54 ? p.title.slice(0, 54) + "…" : p.title}</span>
      <span className="pref-go">↗</span>
    </button>
  );
}

// ── AgentMsg ──────────────────────────────────────────────────────────────────

export interface ChatMessage {
  role: "user" | "assistant";
  text?: string;
  thinking?: boolean | string;
  tools?: ToolEntry[];
  streaming?: boolean;
  papers?: Paper[];
  images?: string[];
}

interface AgentMsgProps {
  m: ChatMessage;
  onOpen: (id: string) => void;
  onStop?: () => void;
}

export function AgentMsg({ m, onOpen, onStop }: AgentMsgProps) {
  if (m.role === "user") {
    return (
      <div className="am user">
        {m.images && m.images.length > 0 && (
          <div className="am-img-strip">
            {m.images.map((src, i) => (
              <img key={i} src={src} alt="" className="am-img-thumb" />
            ))}
          </div>
        )}
        <div className="am-body">{m.text}</div>
      </div>
    );
  }

  const isLoading = m.thinking === true;
  const traceText = typeof m.thinking === "string" && m.thinking ? m.thinking : null;
  const agentTools = (m.tools || []).filter(t => t.id !== undefined);
  const legacyTools = (m.tools || []).filter(t => t.id === undefined);
  const hasProcess = traceText || agentTools.length > 0;

  return (
    <div className="am agent">
      <div className="am-head">
        <span className="am-dot" /> Agent
        {isLoading && <span className="am-thinking">thinking…</span>}
      </div>
      {hasProcess && (
        <AgentProcessPill thinking={traceText} tools={agentTools} streaming={m.streaming} onStop={onStop} />
      )}
      {legacyTools.length > 0 && (
        <div className="am-tools-list">
          {legacyTools.map((t, i) => <ToolCall key={i} {...t} />)}
        </div>
      )}
      {m.text && (
        <div className="am-body">
          <ChatMd text={m.text} streaming={m.streaming} />
        </div>
      )}
      {!m.text && !m.streaming && !agentTools.length && !traceText && (
        <div className="am-body" style={{ color: "var(--ink-4)", fontStyle: "italic" }}>No response.</div>
      )}
      {m.papers && m.papers.length > 0 && (
        <div className="am-papers">
          {m.papers.map(p => <PaperRef key={p.id} p={p} onOpen={onOpen} />)}
        </div>
      )}
    </div>
  );
}
