import { useState, useEffect, useRef } from "react";
import { api } from "../api";
import type { Paper, BatchStatus } from "../types";

// ── Timeline ──────────────────────────────────────────────────────────────────

interface TimelineViewProps {
  papers: (Paper & { citedBy?: number })[];
  activeId: string | null;
  onSelect: (id: string) => void;
}

export function TimelineView({ papers, activeId, onSelect }: TimelineViewProps) {
  const W = 900, H = 560;
  const pad = { l: 140, r: 30, t: 40, b: 60 };
  const lanes = [
    { key: "LLM",   label: "LLM / Efficiency",       topics: ["LLM", "Efficiency", "Architecture", "Training"] },
    { key: "INTERP",label: "Interpretability",        topics: ["Interpretability"] },
    { key: "RL",    label: "RL / World models",       topics: ["RL", "World Models"] },
    { key: "GEO",   label: "Geometric / Foundations", topics: ["Geometric", "Foundations", "Representation"] },
    { key: "BIO",   label: "Bio",                     topics: ["Bio", "Imaging", "Foundation"] },
    { key: "GEN",   label: "Generative",              topics: ["Generative", "Audio"] },
  ];
  const laneFor = (p: Paper) => lanes.findIndex(l => l.topics.some(t => p.topics.includes(t)));
  const dates = papers.map(p => +new Date(p.date));
  const minT = Math.min(...dates), maxT = Math.max(...dates);
  const xFor = (d: string) => pad.l + ((+new Date(d) - minT) / (maxT - minT)) * (W - pad.l - pad.r);
  const yFor = (li: number) => pad.t + (li + 0.5) * ((H - pad.t - pad.b) / lanes.length);
  const months = ["2025·11", "2025·12", "2026·01", "2026·02", "2026·03", "2026·04"];

  return (
    <div className="timeline-wrap">
      <div className="tl-head">
        <div className="tl-title">Timeline</div>
        <div className="tl-sub">{papers.length} papers · 6 topic lanes · Nov 2025 → Apr 2026</div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" style={{ width: "100%", height: "100%", display: "block" }}>
        {lanes.map((l, i) => (
          <g key={l.key}>
            <rect x={pad.l} y={yFor(i) - 18} width={W - pad.l - pad.r} height={36}
              fill={i % 2 ? "rgba(36,28,18,0.02)" : "transparent"} />
            <text x={pad.l - 14} y={yFor(i) + 4} textAnchor="end" fontSize="11" fill="var(--ink-3)">{l.label}</text>
            <line x1={pad.l} x2={W - pad.r} y1={yFor(i) + 18} y2={yFor(i) + 18} stroke="rgba(36,28,18,0.06)" />
          </g>
        ))}
        {months.map((m, i) => {
          const x = pad.l + (i / (months.length - 1)) * (W - pad.l - pad.r);
          return (
            <g key={m}>
              <line x1={x} x2={x} y1={pad.t - 10} y2={H - pad.b + 6} stroke="rgba(36,28,18,0.05)" />
              <text x={x} y={H - pad.b + 24} textAnchor="middle" fontSize="10" fill="var(--ink-4)">{m}</text>
            </g>
          );
        })}
        {papers.map(p => {
          const li = laneFor(p); if (li < 0) return null;
          const x = xFor(p.date), y = yFor(li);
          const active = p.id === activeId;
          const r = 5 + Math.log1p(p.citedBy ?? 0) * 1.8;
          return (
            <g key={p.id} style={{ cursor: "pointer" }} onClick={() => onSelect(p.id)}>
              <circle cx={x} cy={y} r={r} fill={active ? "var(--rust)" : "var(--bg-3)"} stroke={active ? "var(--ink)" : "var(--bg)"} strokeWidth="1.5" />
              <text x={x + r + 6} y={y + 3} fontSize={active ? 11 : 10} fill={active ? "var(--ink)" : "var(--ink-3)"}>
                {p.title.length > 44 ? p.title.slice(0, 44) + "…" : p.title}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ── Velocity sparkline ────────────────────────────────────────────────────────

export function VelocitySpark({ data = null }: { data?: number[] | null }) {
  const pts = (data && data.length) ? data.slice(0, 12) : Array(12).fill(0) as number[];
  const max = Math.max(...pts, 1);
  const step = 96 / Math.max(pts.length - 1, 1);
  const d = pts.map((v, i) => `${i === 0 ? "M" : "L"} ${i * step} ${24 - (v / max) * 20}`).join(" ");
  const hasData = pts.some(v => v > 0);
  return (
    <svg width="96" height="24" viewBox="0 0 96 24" style={{ flexShrink: 0 }}>
      {hasData
        ? <path d={d} fill="none" stroke="var(--rust)" strokeWidth="1.4" />
        : <line x1="0" y1="20" x2="96" y2="20" stroke="var(--border)" strokeWidth="1" strokeDasharray="3 3" />
      }
      {hasData && pts.map((v, i) => i === pts.length - 1 && (
        <circle key={i} cx={i * step} cy={24 - (v / max) * 20} r="2" fill="var(--rust)" />
      ))}
    </svg>
  );
}

// ── Batch actions ─────────────────────────────────────────────────────────────

const BATCH_ACTIONS = [
  { id: "ocr",               label: "OCR only",                sub: "Vision LLM extracts text from PDF page images" },
  { id: "summarize",         label: "Summarize only",          sub: "Run LLM summary on existing text or abstract" },
  { id: "ocr_summarize",     label: "OCR + Summarize",         sub: "Full pipeline — vision OCR then summarize" },
  { id: "extract",           label: "PDF extract only",        sub: "Fast — reads embedded text directly, no AI needed" },
  { id: "extract_summarize", label: "PDF Extract + Summarize", sub: "Extract text then summarize — no OCR model needed" },
  { id: "embed",             label: "Embed only",              sub: "Generate semantic embeddings via configured embedding provider" },
  { id: "download_pdf",      label: "Download PDFs",           sub: "Fetch PDFs for papers that don't have them yet" },
];

const BATCH_FILTERS = [
  { id: "no_ocr",     label: "Missing full text",  sub: "Papers without OCR text yet" },
  { id: "no_summary", label: "Missing summary",    sub: "Papers without an LLM summary" },
  { id: "no_embed",   label: "Missing embeddings", sub: "Papers not yet embedded for semantic search" },
  { id: "no_pdf",     label: "Missing PDFs",       sub: "Papers without a downloaded PDF" },
  { id: "all",        label: "All papers",         sub: "Reprocess every paper in the database" },
];

// ── AgentWatch ────────────────────────────────────────────────────────────────

interface AgentWatchProps {
  open: boolean;
  onClose: () => void;
}

export function AgentWatch({ open, onClose }: AgentWatchProps) {
  const [action, setAction] = useState("ocr_summarize");
  const [filter, setFilter] = useState("no_ocr");
  const [status, setStatus] = useState<BatchStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [startMsg, setStartMsg] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPoll = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  };

  useEffect(() => {
    if (!open) { stopPoll(); return; }
    const refresh = () => api.fetchBatchStatus().then(s => s && setStatus(s));
    refresh();
    pollRef.current = setInterval(refresh, 2000);
    return stopPoll;
  }, [open]);

  const start = async () => {
    setStarting(true); setStartMsg(null);
    try {
      const raw = await api.startBatch(action, filter);
      const res = raw as { queued?: number } | null;
      setStartMsg(res ? `Queued ${res.queued ?? 0} paper${res.queued !== 1 ? "s" : ""}.` : "Failed to start — check server logs.");
    } catch (e) { setStartMsg(`Error: ${e}`); }
    setStarting(false);
  };

  const isRunning = status?.running;
  const pct = isRunning && status!.total > 0 ? Math.round((status!.done / status!.total) * 100) : 0;
  const actionLabel: Record<string, string> = {
    ocr: "OCR", summarize: "Summarising", ocr_summarize: "OCR + Summarising",
    extract: "Extracting", extract_summarize: "Extracting + Summarising",
    embed: "Embedding", download_pdf: "Downloading PDFs",
  };

  return (
    <div className={"watch-drawer " + (open ? "open" : "")}>
      <div className="watch-head">
        <span style={{ width: 8, height: 8, background: isRunning ? "var(--rust)" : "var(--bg-3)", borderRadius: "50%", flexShrink: 0,
                       boxShadow: isRunning ? "0 0 0 3px rgba(162,62,34,0.18)" : "none", transition: "all 0.3s" }} />
        Batch processing
        {isRunning && <span className="count-badge">{status!.done}/{status!.total}</span>}
        <span className="close" onClick={onClose}>✕</span>
      </div>
      <div className="watch-body">
        {isRunning && (
          <>
            <div className="watch-kicker">{actionLabel[status!.action] ?? ""} in progress</div>
            <div className="watch-digest" style={{ marginBottom: 18 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                <span style={{ fontWeight: 600 }}>{pct}% complete</span>
                <span style={{ color: "var(--ink-4)" }}>{status!.done} done · {status!.errors > 0 ? `${status!.errors} errors` : "no errors"}</span>
              </div>
              <div style={{ height: 3, background: "var(--bg-3)", borderRadius: 2, marginBottom: 8 }}>
                <div style={{ height: "100%", width: pct + "%", background: "var(--rust)", borderRadius: 2, transition: "width 0.5s ease" }} />
              </div>
              {status!.current && (
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--ink-4)",
                              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  ⌖ {status!.current}
                </div>
              )}
            </div>
          </>
        )}
        <div className="watch-list-head">Action</div>
        {BATCH_ACTIONS.map(a => (
          <div key={a.id} className="watch-item" style={{ cursor: "pointer" }} onClick={() => !isRunning && setAction(a.id)}>
            <span className={"watch-toggle " + (action === a.id ? "on" : "")}><span /></span>
            <div className="watch-main">
              <div className="watch-name">{a.label}</div>
              <div className="watch-sub">{a.sub}</div>
            </div>
          </div>
        ))}
        <div className="watch-list-head" style={{ marginTop: 14 }}>Target papers</div>
        {BATCH_FILTERS.map(f => (
          <div key={f.id} className="watch-item" style={{ cursor: "pointer" }} onClick={() => !isRunning && setFilter(f.id)}>
            <span className={"watch-toggle " + (filter === f.id ? "on" : "")}><span /></span>
            <div className="watch-main">
              <div className="watch-name">{f.label}</div>
              <div className="watch-sub">{f.sub}</div>
            </div>
          </div>
        ))}
        {startMsg && <div className="watch-digest" style={{ marginTop: 14, marginBottom: 0 }}>{startMsg}</div>}
        <button
          className={isRunning ? "ghost" : "primary"}
          disabled={starting || !!isRunning}
          onClick={start}
          style={{ width: "100%", marginTop: 16, justifyContent: "center" }}>
          {isRunning ? `Running… ${pct}%` : starting ? "Starting…" : "▶  Start batch"}
        </button>
        <div className="watch-kicker" style={{ marginTop: 14, lineHeight: 1.6, textTransform: "none", letterSpacing: 0, fontSize: 11 }}>
          Papers are processed one at a time. OCR requires a multimodal model in <b>⚙ Settings → OCR</b>.
        </div>
      </div>
    </div>
  );
}

// ── RoutingMenu ───────────────────────────────────────────────────────────────

interface RoutingMenuProps {
  open: boolean;
  onClose: () => void;
  routing: string;
  setRouting: (v: string) => void;
}

export function RoutingMenu({ open, onClose, routing, setRouting }: RoutingMenuProps) {
  if (!open) return null;
  const options = [
    { id: "local",  name: "Local · vLLM",  model: "llama-3.1-70B",    cost: "0 ¢",          speed: "14 tok/s", privacy: "Full" },
    { id: "claude", name: "Claude Sonnet", model: "sonnet-4.5",       cost: "0.3 ¢/paper",  speed: "68 tok/s", privacy: "API" },
    { id: "gpt",    name: "GPT-4.1",       model: "gpt-4.1",          cost: "0.4 ¢/paper",  speed: "72 tok/s", privacy: "API" },
    { id: "gemini", name: "Gemini 2.5",    model: "gemini-2.5-pro",   cost: "0.2 ¢/paper",  speed: "85 tok/s", privacy: "API" },
  ];
  return (
    <div className="routing-menu">
      <div className="routing-head">
        Model routing
        <span className="close" onClick={onClose}>✕</span>
      </div>
      <div className="routing-note">Choose how papers are summarised. Default is local. Paywalled / large PDFs fall back to API if allowed.</div>
      {options.map(o => (
        <div key={o.id} className={"routing-opt " + (routing === o.id ? "on" : "")} onClick={() => { setRouting(o.id); onClose(); }}>
          <div className="routing-dot">{routing === o.id ? "●" : "○"}</div>
          <div className="routing-main">
            <div className="routing-name">{o.name} <span className="routing-model">{o.model}</span></div>
            <div className="routing-stats">
              <span>{o.cost}</span><span>·</span><span>{o.speed}</span><span>·</span>
              <span style={{ color: o.privacy === "Full" ? "var(--ok)" : "var(--ember)" }}>{o.privacy === "Full" ? "✓ Private" : "↗ API"}</span>
            </div>
          </div>
        </div>
      ))}
      <div className="routing-foot">
        <label><input type="checkbox" defaultChecked /> Allow API fallback for paywalled papers</label>
      </div>
    </div>
  );
}
