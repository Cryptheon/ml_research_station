import { useState, useRef, useEffect } from "react";
import { api, API_BASE } from "../api";
import type { Paper, Chat } from "../types";
import { Accordion, CitationVelocity, CacheStatus, ExportPanel } from "./RightRail";

// ── Local types ───────────────────────────────────────────────────────────────

interface TraversalNode {
  id: string;
  title?: string;
  depth: number;
  via_edge_type?: string;
  via_direction?: string;
  via_confidence?: number;
  via_description?: string;
}

export interface TraversalResult {
  start_id: string;
  start_title?: string;
  total_nodes: number;
  stopped_reason?: string;
  nodes_visited: TraversalNode[];
  edges_walked: Array<{ from_id: string; to_id: string }>;
  params?: { edge_types?: string[]; max_depth?: number };
  created_at?: string;
}

// ── PaperQuickActions ─────────────────────────────────────────────────────────

function PaperQuickActions({ paper }: { paper: Paper }) {
  const [flags, setFlags] = useState(paper.cache_flags || 0);
  const [ocrState, setOcrState] = useState("idle");
  const [ocrProg, setOcrProg] = useState({ done: 0, total: 0 });
  const [sumState, setSumState] = useState("idle");
  const [sumMsg, setSumMsg] = useState<string | null>(null);
  const [extractState, setExtractState] = useState("idle");
  const [embedState, setEmbedState] = useState("idle");
  const pollsRef = useRef<ReturnType<typeof setInterval>[]>([]);
  const enc = encodeURIComponent(paper.id);

  const clearPolls = () => { pollsRef.current.forEach(clearInterval); pollsRef.current = []; };

  useEffect(() => {
    setFlags(paper.cache_flags || 0);
    setOcrState("idle"); setSumState("idle"); setExtractState("idle"); setEmbedState("idle"); setSumMsg(null);
    const refreshFlags = () => {
      fetch(`${API_BASE}/papers/${enc}/cache`).then(r => r.ok ? r.json() : null).then((c: Record<string, boolean> | null) => {
        if (c) setFlags((c.pdf ? 1 : 0) | (c.embeddings ? 2 : 0) | (c.summary ? 4 : 0) | (c.fulltext ? 32 : 0) | (c.figures ? 8 : 0) | (c.references ? 16 : 0));
      }).catch(() => {});
    };
    refreshFlags();
    const handler = (e: Event) => { if ((e as CustomEvent).detail.paperId === paper.id) refreshFlags(); };
    window.addEventListener("rs:paper-processed", handler);
    return () => { clearPolls(); window.removeEventListener("rs:paper-processed", handler); };
  }, [paper.id]);

  const hasPdf = Boolean(flags & 1);
  const hasText = Boolean(flags & 32);
  const hasSummary = Boolean(flags & 4) || paper.status === "summarized";

  const triggerOcr = () => {
    setOcrState("running"); setOcrProg({ done: 0, total: 0 });
    fetch(`${API_BASE}/papers/${enc}/ocr`, { method: "POST" }).then(r => {
      if (!r.ok) { setOcrState("error"); return; }
      const cachePoll = setInterval(() => {
        fetch(`${API_BASE}/papers/${enc}/cache`).then(r => r.ok ? r.json() : null).then((c: Record<string, boolean> | null) => {
          if (c?.fulltext) {
            clearPolls(); setOcrState("done"); setFlags(f => f | 32);
            window.dispatchEvent(new CustomEvent("rs:ocr-complete", { detail: { paperId: paper.id } }));
            window.dispatchEvent(new CustomEvent("rs:paper-processed", { detail: { paperId: paper.id } }));
          }
        });
      }, 5000);
      const progPoll = setInterval(() => {
        fetch(`${API_BASE}/papers/${enc}/ocr/progress`).then(r => r.ok ? r.json() : null).then((p: { pages_done: number; pages_total: number } | null) => {
          if (!p) return;
          setOcrProg({ done: p.pages_done, total: p.pages_total });
          window.dispatchEvent(new CustomEvent("rs:ocr-progress", { detail: { paperId: paper.id, pagesDone: p.pages_done, pagesTotal: p.pages_total } }));
        });
      }, 2000);
      pollsRef.current = [cachePoll, progPoll];
    }).catch(() => setOcrState("error"));
  };

  const triggerExtract = () => {
    setExtractState("running");
    api.startBatch("extract", "all", [paper.id]).then(raw => { const res = raw as { queued: number } | null;
      if (!res || res.queued === 0) { setExtractState("error"); return; }
      const poll = setInterval(() => {
        fetch(`${API_BASE}/papers/${enc}/cache`).then(r => r.ok ? r.json() : null).then((c: Record<string, boolean> | null) => {
          if (c?.fulltext) {
            clearInterval(poll); setExtractState("done"); setFlags(f => f | 32);
            window.dispatchEvent(new CustomEvent("rs:ocr-complete", { detail: { paperId: paper.id } }));
            window.dispatchEvent(new CustomEvent("rs:paper-processed", { detail: { paperId: paper.id } }));
          }
        });
      }, 3000);
      pollsRef.current.push(poll);
      setTimeout(() => { clearInterval(poll); setExtractState(s => s === "running" ? "idle" : s); }, 120000);
    }).catch(() => setExtractState("error"));
  };

  const triggerSummarise = () => {
    setSumState("running"); setSumMsg(null);
    fetch(`${API_BASE}/papers/${enc}/reader/regenerate`, { method: "POST" }).then(async r => {
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        setSumMsg((body as { detail?: string }).detail || "Summarise failed.");
        setSumState("error"); return;
      }
      const poll = setInterval(() => {
        api.fetchReader(paper.id).then(raw => {
          const d = raw as { reader_meta?: { model?: string } } | null;
          if (d?.reader_meta?.model) {
            clearInterval(poll); setSumState("done");
            window.dispatchEvent(new CustomEvent("rs:paper-processed", { detail: { paperId: paper.id } }));
            window.dispatchEvent(new CustomEvent("rs:refreshPapers"));
          }
        });
      }, 5000);
      pollsRef.current.push(poll);
      setTimeout(() => { clearInterval(poll); setSumState(s => s === "running" ? "idle" : s); }, 300000);
    }).catch(() => { setSumMsg("Network error."); setSumState("error"); });
  };

  const triggerEmbed = () => {
    setEmbedState("running");
    fetch(`${API_BASE}/papers/${enc}/embed`, { method: "POST" }).then(r => {
      if (!r.ok) { setEmbedState("error"); return; }
      const poll = setInterval(() => {
        fetch(`${API_BASE}/papers/${enc}/cache`).then(r => r.ok ? r.json() : null).then((c: Record<string, boolean> | null) => {
          if (c?.embeddings) {
            clearInterval(poll); setFlags(f => f | 2); setEmbedState("done");
            setTimeout(() => setEmbedState("idle"), 3000);
            window.dispatchEvent(new CustomEvent("rs:paper-processed", { detail: { paperId: paper.id } }));
          }
        }).catch(() => {});
      }, 2000);
      pollsRef.current.push(poll);
      setTimeout(() => { clearInterval(poll); setEmbedState(s => s === "running" ? "idle" : s); }, 300000);
    }).catch(() => setEmbedState("error"));
  };

  const Btn = ({ label, state, onClick, disabled, title }: {
    label: { icon: string; text: string }; state: string;
    onClick: () => void; disabled?: boolean; title?: string;
  }) => {
    const color = state === "done" ? "var(--ok)" : state === "error" ? "var(--rust)" : "var(--ink-3)";
    const bg = state === "done" ? "rgba(0,160,80,0.07)" : state === "error" ? "rgba(162,62,34,0.07)" : "var(--bg-2)";
    return (
      <button onClick={onClick} disabled={disabled || state === "running"} title={title} style={{
        flex: 1, padding: "6px 0", fontSize: 11, fontWeight: 500,
        border: "1px solid var(--rule)", borderRadius: 5,
        background: bg, color, cursor: "pointer",
        display: "flex", flexDirection: "column", alignItems: "center", gap: 2,
        transition: "background 0.15s",
        opacity: (disabled || state === "running") ? 0.6 : 1,
      }}>
        <span style={{ fontSize: 14 }}>{state === "running" ? "…" : state === "done" ? "✓" : state === "error" ? "!" : label.icon}</span>
        <span style={{ fontSize: 10, letterSpacing: 0.2 }}>{label.text}</span>
      </button>
    );
  };

  const ocrLabel = ocrState === "running" && ocrProg.total > 0
    ? `${ocrProg.done}/${ocrProg.total}` : hasText ? "Re-OCR" : "OCR";

  return (
    <div style={{ padding: "0 14px 14px" }}>
      <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: 0.8, color: "var(--ink-4)", textTransform: "uppercase", marginBottom: 8 }}>Quick actions</div>
      <div style={{ display: "flex", gap: 6 }}>
        <Btn label={{ icon: "⌖", text: ocrLabel }} state={ocrState} onClick={triggerOcr} disabled={!hasPdf}
          title={hasPdf ? "Run vision OCR on this paper's PDF" : "No PDF cached — ingest first"} />
        <Btn label={{ icon: "⇲", text: hasText ? "Re-extract" : "Extract" }} state={extractState} onClick={triggerExtract} disabled={!hasPdf}
          title={hasPdf ? "Extract embedded text from PDF (fast, no AI)" : "No PDF cached"} />
        <Btn label={{ icon: "◈", text: hasSummary ? "Re-sum." : "Summarize" }} state={sumState} onClick={triggerSummarise}
          title="Generate or regenerate LLM summary" />
        <Btn label={{ icon: "↺", text: "Embed" }} state={embedState} onClick={triggerEmbed}
          title="Re-compute embeddings for this paper" />
      </div>
      {sumMsg && sumState === "error" && (
        <div style={{ marginTop: 6, fontSize: 11, color: "var(--rust)", lineHeight: 1.4 }}>{sumMsg}</div>
      )}
      <div style={{ display: "flex", gap: 10, marginTop: 8, fontSize: 10, color: "var(--ink-4)" }}>
        <span style={{ color: hasPdf ? "var(--ok)" : "var(--rule-2)" }}>● PDF</span>
        <span style={{ color: hasText ? "var(--ok)" : "var(--rule-2)" }}>● Text</span>
        <span style={{ color: hasSummary ? "var(--ok)" : "var(--rule-2)" }}>● Summary</span>
        <span style={{ color: Boolean(flags & 2) ? "var(--ok)" : "var(--rule-2)" }}>● Embedded</span>
      </div>
    </div>
  );
}

// ── PaperChatsSection ─────────────────────────────────────────────────────────

const CHAT_PAGE_SIZE = 3;

interface ChatWithMeta extends Chat {
  last_message?: string;
  message_count?: number;
}

function PaperChatsSection({ paper }: { paper: Paper }) {
  const [chats, setChats] = useState<ChatWithMeta[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);

  useEffect(() => {
    if (!paper) return;
    setLoaded(false); setChats([]); setQ(""); setPage(0);
    api.fetchPaperChats(paper.id).then((cs: ChatWithMeta[]) => {
      setChats(cs || []); setLoaded(true);
    }).catch(() => setLoaded(true));
  }, [paper?.id]);

  useEffect(() => {
    const handler = () => {
      if (!paper) return;
      api.fetchPaperChats(paper.id).then((cs: ChatWithMeta[]) => setChats(cs || [])).catch(() => {});
    };
    document.addEventListener("rs:chat-saved", handler);
    return () => document.removeEventListener("rs:chat-saved", handler);
  }, [paper?.id]);

  const openChat = (chatId: string) => { document.dispatchEvent(new CustomEvent("rs:open-chat", { detail: { chatId } })); };
  const newChat = () => { document.dispatchEvent(new CustomEvent("rs:open-chat", { detail: { chatId: null } })); };

  if (!loaded) return null;

  const chatAgo = (updatedAt: string) => {
    const mins = Math.floor((Date.now() - new Date(updatedAt).getTime()) / 60000);
    if (mins < 2) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  };

  const filtered = q.trim()
    ? chats.filter(c => (c.title || "").toLowerCase().includes(q.toLowerCase()) || (c.last_message || "").toLowerCase().includes(q.toLowerCase()))
    : chats;
  const totalPages = Math.ceil(filtered.length / CHAT_PAGE_SIZE);
  const paginated = filtered.slice(page * CHAT_PAGE_SIZE, page * CHAT_PAGE_SIZE + CHAT_PAGE_SIZE);
  const setSearch = (val: string) => { setQ(val); setPage(0); };

  return (
    <Accordion title="Chats" count={chats.length} defaultOpen={chats.length > 0}>
      <div style={{ padding: "4px 14px 10px" }}>
        <div style={{ position: "relative", marginBottom: 10 }}>
          <span style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", fontSize: 11, color: "var(--ink-4)", pointerEvents: "none" }}>⌕</span>
          <input value={q} onChange={e => setSearch(e.target.value)} placeholder="Search conversations…"
            style={{ width: "100%", boxSizing: "border-box", padding: "5px 8px 5px 24px", fontSize: 12, background: "var(--bg-2)", border: "1px solid var(--rule)", borderRadius: 4, color: "var(--ink-1)", outline: "none" }} />
          {q && <span onClick={() => setSearch("")} style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", fontSize: 11, color: "var(--ink-4)", cursor: "pointer" }}>✕</span>}
        </div>
        {filtered.length === 0
          ? <div style={{ fontSize: 12, color: "var(--ink-4)", marginBottom: 10 }}>{q ? "No matching conversations." : "No conversations yet."}</div>
          : paginated.map(chat => (
            <div key={chat.id} onClick={() => openChat(chat.id)}
              style={{ padding: "7px 10px", marginBottom: 5, borderRadius: 5, background: "var(--bg-2)", border: "1px solid var(--rule)", cursor: "pointer", transition: "background 0.15s" }}
              onMouseEnter={e => (e.currentTarget.style.background = "var(--bg-3)")}
              onMouseLeave={e => (e.currentTarget.style.background = "var(--bg-2)")}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 6 }}>
                <span style={{ fontWeight: 600, fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{chat.title || "Conversation"}</span>
                <span style={{ fontSize: 10, color: "var(--ink-4)", flexShrink: 0 }}>{chatAgo(chat.updated_at)}</span>
              </div>
              {chat.last_message && <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{chat.last_message}</div>}
              <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 2 }}>{chat.message_count ?? 0} msg{(chat.message_count ?? 0) !== 1 ? "s" : ""}</div>
            </div>
          ))
        }
        {totalPages > 1 && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 6, marginBottom: 2 }}>
            <button className="ghost" style={{ fontSize: 11, padding: "2px 8px" }} disabled={page === 0} onClick={() => setPage(p => p - 1)}>‹ Prev</button>
            <span style={{ fontSize: 11, color: "var(--ink-4)" }}>{page + 1} / {totalPages}</span>
            <button className="ghost" style={{ fontSize: 11, padding: "2px 8px" }} disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>Next ›</button>
          </div>
        )}
        <button className="ghost" style={{ fontSize: 12, marginTop: 6 }} onClick={newChat}>+ New conversation</button>
      </div>
    </Accordion>
  );
}

// ── TraversalTrail ────────────────────────────────────────────────────────────

const _TRAIL_DEPTH_COLORS = ["#f0b840", "#e07020", "#c04040", "#8040c0", "#3070b0"];
const _TRAIL_EDGE_COLORS: Record<string, string> = {
  extends: "#5a8af0", supersedes: "#b06af0", challenges: "#e05a5a",
  uses: "#3ab8c8", applies: "#3a8a6a", surveys: "#e0a040",
  baseline: "#8a7f74", concurrent: "#c8aa32",
  cites: "#a08060", cited_by: "#a08060", semantic: "#3a8a6a",
};
const _TRAIL_PAGE_SIZE = 5;

function TraversalTrail({ traversal, onSelect }: { traversal: TraversalResult; onSelect?: (id: string) => void }) {
  const [page, setPage] = useState(0);
  if (!traversal?.start_id) return null;
  const depthCol = (d: number) => _TRAIL_DEPTH_COLORS[Math.min(d, _TRAIL_DEPTH_COLORS.length - 1)];
  const ordered = [...(traversal.nodes_visited || [])].sort((a, b) => a.depth - b.depth);
  const totalPages = Math.ceil(ordered.length / _TRAIL_PAGE_SIZE);
  const pageNodes = ordered.slice(page * _TRAIL_PAGE_SIZE, (page + 1) * _TRAIL_PAGE_SIZE);

  const renderNode = (n: TraversalNode, isLast: boolean) => {
    const col = depthCol(n.depth);
    const edgeCol = _TRAIL_EDGE_COLORS[n.via_edge_type ?? ""] || "#8a7f74";
    return (
      <div key={n.id} style={{ padding: "0 18px", paddingBottom: isLast ? 4 : 6, borderBottom: isLast ? "none" : "1px solid var(--rule)", marginBottom: isLast ? 0 : 2 }}>
        {n.via_edge_type && (
          <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 3, paddingLeft: 11 }}>
            <div style={{ width: 12, height: 1, background: "var(--rule-2)" }} />
            <span style={{ fontSize: 8, fontWeight: 600, color: edgeCol, background: edgeCol + "22", borderRadius: 2, padding: "0 4px", lineHeight: "15px" }}>{n.via_edge_type}</span>
            <span style={{ fontSize: 8, color: "var(--ink-4)" }}>{n.via_direction === "incoming" ? "←" : "→"}</span>
            {n.via_confidence != null && <span style={{ fontSize: 8, color: "var(--ink-4)", fontFamily: "var(--font-mono)" }}>{n.via_confidence.toFixed(2)}</span>}
          </div>
        )}
        <div style={{ display: "flex", alignItems: "flex-start", gap: 7 }}>
          <div style={{ width: 7, height: 7, borderRadius: "50%", background: col, flexShrink: 0, marginTop: 3 }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 9, color: "var(--ink-4)", marginBottom: 1 }}>depth {n.depth}</div>
            <div style={{ fontSize: 10.5, lineHeight: 1.35, cursor: onSelect ? "pointer" : "default" }} onClick={() => onSelect?.(n.id)}>
              {n.title || n.id}
            </div>
            {n.via_description && <div style={{ fontSize: 8.5, color: "var(--ink-4)", fontStyle: "italic", lineHeight: 1.3, marginTop: 2 }}>
              {n.via_description.length > 90 ? n.via_description.slice(0, 87) + "…" : n.via_description}
            </div>}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div style={{ padding: "0 0 8px" }}>
      <div style={{ padding: "0 18px 8px", borderBottom: "1px solid var(--rule)", marginBottom: 8 }}>
        <span style={{ fontSize: 10, color: "var(--ink-4)" }}>
          {traversal.total_nodes} node{traversal.total_nodes !== 1 ? "s" : ""}
          {traversal.stopped_reason && traversal.stopped_reason !== "exhausted" ? ` · stopped: ${traversal.stopped_reason}` : " · exhausted"}
        </span>
      </div>
      <div style={{ padding: "0 18px 6px", borderBottom: "1px solid var(--rule)", marginBottom: 4 }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 7 }}>
          <div style={{ width: 7, height: 7, borderRadius: "50%", background: depthCol(0), flexShrink: 0, marginTop: 3, boxShadow: "0 0 4px " + depthCol(0) + "88" }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 9, color: "var(--ink-4)", marginBottom: 1 }}>depth 0 · start</div>
            <div style={{ fontSize: 10.5, lineHeight: 1.35, cursor: onSelect ? "pointer" : "default", fontWeight: 500 }} onClick={() => onSelect?.(traversal.start_id)}>
              {traversal.start_title || traversal.start_id}
            </div>
          </div>
        </div>
      </div>
      {pageNodes.map((n, i) => renderNode(n, i === pageNodes.length - 1 && totalPages <= 1))}
      {totalPages > 1 && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "6px 18px 0", borderTop: "1px solid var(--rule)", marginTop: 4 }}>
          <button className="ghost small" style={{ fontSize: 9, padding: "2px 7px" }} disabled={page === 0} onClick={() => setPage(p => p - 1)}>‹ prev</button>
          <span style={{ fontSize: 9, color: "var(--ink-4)" }}>{page + 1} / {totalPages}</span>
          <button className="ghost small" style={{ fontSize: 9, padding: "2px 7px" }} disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>next ›</button>
        </div>
      )}
    </div>
  );
}

// ── TraversalSection ──────────────────────────────────────────────────────────

function TraversalSection({ paper, selectedTraversal, onSelectTraversal, onSelect }: {
  paper: Paper | null;
  selectedTraversal: TraversalResult | null;
  onSelectTraversal: (t: TraversalResult | null) => void;
  onSelect: (id: string) => void;
}) {
  const [allTraversals, setAllTraversals] = useState<TraversalResult[]>([]);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const fetchNow = () => { api.fetchTraversals().then(raw => setAllTraversals((raw as unknown as TraversalResult[]) || [])).catch(() => {}); };
    fetchNow();
    pollRef.current = setInterval(fetchNow, 4000);
    window.addEventListener("rs:traversal-updated", fetchNow);
    return () => { if (pollRef.current) clearInterval(pollRef.current); window.removeEventListener("rs:traversal-updated", fetchNow); };
  }, []);

  const paperTraversals = allTraversals
    .filter(t => t.start_id === paper?.id || (t.nodes_visited || []).some(n => n.id === paper?.id))
    .slice(0, 20);

  const trailAgo = (iso?: string) => {
    if (!iso) return "";
    const mins = Math.floor((Date.now() - new Date(iso + "Z").getTime()) / 60000);
    if (mins < 2) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  };

  const handleSelect = (t: TraversalResult, idx: number) => {
    if (expandedIdx === idx) { setExpandedIdx(null); onSelectTraversal(null); }
    else { setExpandedIdx(idx); onSelectTraversal(t); }
  };

  const handleDelete = (e: React.MouseEvent, globalIdx: number) => {
    e.stopPropagation();
    api.deleteTraversal(globalIdx).then(() => {
      api.fetchTraversals().then(raw => {
        const ts = (raw as unknown as TraversalResult[]) || [];
        setAllTraversals(ts);
        if (expandedIdx !== null && expandedIdx >= ts.length) { setExpandedIdx(null); onSelectTraversal(null); }
      });
    }).catch(() => {});
  };

  if (paperTraversals.length === 0) return null;

  return (
    <Accordion title="Traversal trails" count={paperTraversals.length} defaultOpen={paperTraversals.length > 0}>
      <div style={{ padding: "4px 14px 10px" }}>
        {paperTraversals.map((t, localIdx) => {
          const globalIdx = allTraversals.indexOf(t);
          const isActive = selectedTraversal === t || expandedIdx === localIdx;
          const depthCol = _TRAIL_DEPTH_COLORS[0];
          const isOrigin = t.start_id === paper?.id;
          return (
            <div key={localIdx}>
              <div onClick={() => handleSelect(t, localIdx)} style={{
                padding: "7px 10px", marginBottom: isActive ? 0 : 5,
                borderRadius: isActive ? "5px 5px 0 0" : 5,
                background: isActive ? "var(--bg-3)" : "var(--bg-2)",
                border: isActive ? `1px solid ${depthCol}66` : "1px solid var(--rule)",
                borderBottom: isActive ? "none" : undefined,
                cursor: "pointer", transition: "background 0.15s",
                display: "flex", alignItems: "flex-start", gap: 8,
              }}
                onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = "var(--bg-3)"; }}
                onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = "var(--bg-2)"; }}>
                <div style={{ width: 7, height: 7, borderRadius: "50%", background: depthCol, flexShrink: 0, marginTop: 4, boxShadow: isActive ? `0 0 5px ${depthCol}` : "none" }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 4 }}>
                    <span style={{ fontSize: 11, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
                      {t.total_nodes} node{t.total_nodes !== 1 ? "s" : ""}{t.stopped_reason && t.stopped_reason !== "exhausted" ? ` · ${t.stopped_reason}` : ""}
                    </span>
                    <span style={{ fontSize: 9, color: "var(--ink-4)", flexShrink: 0 }}>{trailAgo(t.created_at)}</span>
                  </div>
                  <div style={{ fontSize: 10, color: "var(--ink-3)", marginTop: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {(t.params?.edge_types || []).join(", ") || "all edges"}{" · depth "}{t.params?.max_depth ?? "?"}
                  </div>
                  {!isOrigin && <div style={{ fontSize: 9, color: "var(--accent)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>via {t.start_title || t.start_id}</div>}
                </div>
                <button onClick={e => handleDelete(e, globalIdx)} className="ghost small"
                  style={{ fontSize: 9, padding: "1px 5px", flexShrink: 0, color: "var(--ink-4)" }} title="Remove this trail">✕</button>
              </div>
              {isActive && (
                <div style={{ border: `1px solid ${depthCol}66`, borderTop: "none", borderRadius: "0 0 5px 5px", marginBottom: 5, background: "var(--bg-2)" }}>
                  <TraversalTrail key={t.created_at} traversal={t} onSelect={onSelect} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Accordion>
  );
}

// ── WebScreenshotsSection ─────────────────────────────────────────────────────

interface Screenshot { index: number; url: string }

function WebScreenshotsSection({ paper }: { paper: Paper }) {
  const [shots, setShots] = useState<Screenshot[]>([]);
  const [zoomed, setZoomed] = useState<Screenshot | null>(null);

  useEffect(() => {
    if (!paper?.id?.startsWith("web:")) return;
    api.fetchWebScreenshots(paper.id).then(raw => setShots((raw as Screenshot[]) || [])).catch(() => {});
  }, [paper?.id]);

  if (shots.length === 0) return null;

  return (
    <Accordion title="Screenshots" count={shots.length} defaultOpen>
      <div style={{ padding: "6px 14px 10px", display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(100px, 1fr))", gap: 6 }}>
        {shots.map(s => (
          <div key={s.index} onClick={() => setZoomed(s)}
            style={{ position: "relative", borderRadius: 4, overflow: "hidden", border: "1px solid var(--rule)", cursor: "zoom-in", background: "var(--bg-2)", aspectRatio: "16/9" }}>
            <img src={s.url} alt={`Viewport ${s.index + 1}`} style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} loading="lazy" />
            <div style={{ position: "absolute", bottom: 2, right: 4, fontSize: 9, color: "rgba(247,242,232,0.7)", textShadow: "0 1px 2px rgba(0,0,0,0.8)" }}>{s.index + 1}</div>
          </div>
        ))}
      </div>
      {zoomed && (
        <div onClick={() => setZoomed(null)} style={{ position: "fixed", inset: 0, zIndex: 9999, background: "rgba(14,11,7,0.88)", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12 }}>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <button className="ghost" onClick={e => { e.stopPropagation(); setZoomed(shots[Math.max(0, zoomed.index - 1)]); }} disabled={zoomed.index === 0} style={{ fontSize: 18, padding: "4px 10px" }}>‹</button>
            <span style={{ fontSize: 12, color: "var(--ink-3)" }}>{zoomed.index + 1} / {shots.length}</span>
            <button className="ghost" onClick={e => { e.stopPropagation(); setZoomed(shots[Math.min(shots.length - 1, zoomed.index + 1)]); }} disabled={zoomed.index === shots.length - 1} style={{ fontSize: 18, padding: "4px 10px" }}>›</button>
          </div>
          <img src={zoomed.url} alt={`Viewport ${zoomed.index + 1}`} onClick={e => e.stopPropagation()}
            style={{ maxWidth: "90vw", maxHeight: "80vh", borderRadius: 6, border: "1px solid var(--rule)", boxShadow: "0 8px 40px rgba(0,0,0,0.6)" }} />
          <div style={{ display: "flex", gap: 10 }}>
            <a href={zoomed.url} download={`viewport_${zoomed.index + 1}.jpg`} onClick={e => e.stopPropagation()} className="ghost" style={{ fontSize: 12, textDecoration: "none", padding: "4px 12px" }}>↓ Download</a>
            <button className="ghost" onClick={() => setZoomed(null)} style={{ fontSize: 12 }}>✕ Close</button>
          </div>
        </div>
      )}
    </Accordion>
  );
}

// ── WebSection ────────────────────────────────────────────────────────────────

interface WebPage { url: string; title?: string; created_at?: string }

function WebSection({ paper }: { paper: Paper }) {
  const [pages, setPages] = useState<WebPage[]>([]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!paper?.id) return;
    const load = () => { api.fetchWebPages(paper.id).then(raw => setPages((raw as WebPage[]) || [])).catch(() => {}); };
    load();
    pollRef.current = setInterval(load, 5000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [paper?.id]);

  if (pages.length === 0) return null;

  const ago = (iso?: string) => {
    if (!iso) return "";
    const mins = Math.floor((Date.now() - new Date(iso + "Z").getTime()) / 60000);
    if (mins < 2) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  };

  return (
    <Accordion title="Web" count={pages.length} defaultOpen>
      <div style={{ padding: "4px 14px 10px", display: "flex", flexDirection: "column", gap: 5 }}>
        {pages.map((p, i) => (
          <a key={i} href={p.url} target="_blank" rel="noopener noreferrer"
            style={{ display: "block", textDecoration: "none", padding: "7px 10px", borderRadius: 5, background: "var(--bg-2)", border: "1px solid var(--rule)", transition: "background 0.15s" }}
            onMouseEnter={e => (e.currentTarget.style.background = "var(--bg-3)")}
            onMouseLeave={e => (e.currentTarget.style.background = "var(--bg-2)")}>
            <div style={{ fontSize: 11, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.title || p.url}</div>
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 2 }}>
              <span style={{ fontSize: 9, color: "var(--ink-4)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{p.url}</span>
              <span style={{ fontSize: 9, color: "var(--ink-4)", flexShrink: 0, marginLeft: 6 }}>{ago(p.created_at)}</span>
            </div>
          </a>
        ))}
      </div>
    </Accordion>
  );
}

// ── ContextRail ───────────────────────────────────────────────────────────────

interface ContextRailProps {
  paper: Paper | null;
  trace?: unknown;
  mentions?: unknown;
  onSelect: (id: string) => void;
  selectedTraversal: TraversalResult | null;
  onSelectTraversal: (t: TraversalResult | null) => void;
}

export function ContextRail({ paper, onSelect, selectedTraversal, onSelectTraversal }: ContextRailProps) {
  if (!paper) return (
    <div className="pane right ctx-rail">
      <div style={{ padding: 30, color: "var(--ink-4)", fontSize: 12, textAlign: "center" }}>Select a paper to see context.</div>
    </div>
  );

  return (
    <div className="pane right ctx-rail">
      <div className="right-inner">
        <div className="right-summary">
          <div className="rs-id">{paper.id}</div>
          <div className="rs-title">{paper.title}</div>
          <div className="rs-chips">
            {paper.topics.map(t => <span key={t} className="tag">{t}</span>)}
          </div>
        </div>

        <div className="right-stats">
          <div className="stat-card">
            <div className="v">{(paper.scores.relevance * 100).toFixed(0)}</div>
            <div className="k">Relevance</div>
          </div>
          <div className="stat-card">
            <div className="v">{(paper.scores.novelty * 100).toFixed(0)}</div>
            <div className="k">Novelty</div>
          </div>
          <div className="stat-card">
            <div className="v">{(paper.scores.velocity * 100).toFixed(0)}</div>
            <div className="k">Velocity</div>
          </div>
        </div>

        <PaperQuickActions paper={paper} />
        <PaperChatsSection paper={paper} />
        <TraversalSection paper={paper} selectedTraversal={selectedTraversal} onSelectTraversal={onSelectTraversal} onSelect={onSelect} />
        <WebSection paper={paper} />
        {paper.id?.startsWith("web:") && <WebScreenshotsSection paper={paper} />}

        <Accordion title="Citation velocity" defaultOpen>
          <CitationVelocity paper={paper} />
        </Accordion>

        <Accordion title="Cache status">
          <CacheStatus paper={paper} />
        </Accordion>

        <Accordion title="Export">
          <ExportPanel paper={paper} />
        </Accordion>

        <Accordion title="Metadata">
          <div style={{ padding: "4px 18px 14px", fontSize: 12 }}>
            {[
              { label: "Venue", value: paper.venue },
              { label: "Source", value: paper.source },
              { label: "Date", value: paper.date },
            ].map(row => (
              <div key={row.label} style={{ display: "grid", gridTemplateColumns: "90px 1fr", gap: 8, padding: "6px 0", borderBottom: "1px solid var(--rule)" }}>
                <span style={{ color: "var(--ink-4)" }}>{row.label}</span><span>{row.value}</span>
              </div>
            ))}
            {paper.created_at && (
              <div style={{ display: "grid", gridTemplateColumns: "90px 1fr", gap: 8, padding: "6px 0" }}>
                <span style={{ color: "var(--ink-4)" }}>Ingested</span>
                <span title={paper.created_at}>{new Date(paper.created_at).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
              </div>
            )}
          </div>
        </Accordion>
      </div>
    </div>
  );
}
