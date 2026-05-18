import { useState, useEffect } from "react";
import { api, API_BASE } from "../api";
import type { Paper } from "../types";

// ── Accordion ─────────────────────────────────────────────────────────────────

interface AccordionProps {
  title: string;
  count?: number;
  children?: React.ReactNode;
  defaultOpen?: boolean;
}

export function Accordion({ title, count, children, defaultOpen = false }: AccordionProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={"acc " + (open ? "open" : "")}>
      <div className="acc-head" onClick={() => setOpen(!open)}>
        <div className="acc-title">{title}</div>
        {count !== undefined && <div className="acc-count">{count}</div>}
        <div className="acc-chev">›</div>
      </div>
      <div className="acc-body">{children}</div>
    </div>
  );
}

// ── TraceView ─────────────────────────────────────────────────────────────────

interface TraceItem {
  kind: string;
  t: string;
  label: string;
  detail?: string;
}

export function TraceView({ traces }: { traces?: TraceItem[] | null }) {
  if (!traces || !traces.length) return <div style={{ padding: "8px 18px", color: "var(--ink-4)", fontSize: 11 }}>No trace.</div>;
  return (
    <div className="trace">
      {traces.map((t, i) => (
        <div key={i} className={"trace-item kind-" + t.kind}>
          <div className="t">{t.t}</div>
          <div className="node" />
          <div>
            <span className="kind">{t.kind}</span>
            <div className="label">{t.label}</div>
            <div className="detail">{t.detail}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── EmbeddingsMini ────────────────────────────────────────────────────────────

interface EmbPoint {
  x: number;
  y: number;
  is_self?: boolean;
  label?: string;
}

function EmbeddingsMini({ paper }: { paper?: Paper | null }) {
  const [pts, setPts] = useState<EmbPoint[] | null>(null);
  useEffect(() => {
    if (!paper) return;
    api.fetchNeighbors?.(paper.id)
      .then(raw => { const d = raw as { points?: EmbPoint[] } | null; d?.points && d.points.length > 1 ? setPts(d.points) : setPts([]); })
      .catch(() => setPts([]));
  }, [paper?.id]);

  if (!pts || pts.length < 2) {
    return (
      <div className="emb" style={{ display: "flex", alignItems: "center", justifyContent: "center", color: "var(--ink-4)", fontSize: 11, height: 80 }}>
        Embedding neighborhood not yet computed
      </div>
    );
  }

  const self = pts.find(p => p.is_self) || pts[0];
  return (
    <div className="emb">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none">
        {pts.filter(p => !p.is_self).map((p, i) => (
          <line key={i} x1={self.x} y1={self.y} x2={p.x} y2={p.y} stroke="rgba(36,28,18,0.08)" strokeWidth="0.3" />
        ))}
        {pts.map((p, i) => (
          <g key={i}>
            <circle cx={p.x} cy={p.y} r={p.is_self ? 5 : 3} fill={p.is_self ? "var(--rust)" : "var(--bg-3)"} />
            <text x={p.x + 4} y={p.y + 1} fontFamily="var(--font-mono)" fontSize="2.6" fill="var(--ink-3)">{p.label}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}

// ── Mentions ──────────────────────────────────────────────────────────────────

interface Mention {
  src: string;
  count: number;
  last: string;
  note?: string;
}

export function Mentions({ mentions, paper }: { mentions?: Mention[] | null; paper?: Paper | null }) {
  if (!mentions || mentions.length === 0) {
    const arxivId = paper?.id?.startsWith("arxiv:") ? paper.id.slice(6) : null;
    return (
      <div style={{ padding: "0 18px 12px" }}>
        <div style={{ color: "var(--ink-4)", fontSize: 11, marginBottom: 8 }}>Web mention tracking not yet enabled.</div>
        {arxivId && (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <a href={`https://arxiv.org/abs/${arxivId}`} target="_blank" rel="noopener noreferrer"
               style={{ fontSize: 11, color: "var(--rust)", textDecoration: "none" }}>↗ arXiv abstract page</a>
            <a href={`https://scholar.google.com/scholar?q=${encodeURIComponent(paper?.title || "")}`}
               target="_blank" rel="noopener noreferrer"
               style={{ fontSize: 11, color: "var(--rust)", textDecoration: "none" }}>↗ Google Scholar</a>
            <a href={`https://www.semanticscholar.org/search?q=${encodeURIComponent(paper?.title || "")}&sort=Relevance`}
               target="_blank" rel="noopener noreferrer"
               style={{ fontSize: 11, color: "var(--rust)", textDecoration: "none" }}>↗ Semantic Scholar</a>
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="mentions">
      {mentions.map((m, i) => (
        <div key={i} className="mention" title={m.note || ""}>
          <div className="src">{m.src}</div>
          <div className="count">{m.count}</div>
          <div className="last">{m.last}</div>
        </div>
      ))}
      <button className="ghost" style={{ width: "100%", marginTop: 10, justifyContent: "center" }}>↻ Re-run lookup</button>
    </div>
  );
}

// ── CacheStatus ───────────────────────────────────────────────────────────────

interface CacheData {
  pdf: boolean;
  embeddings: boolean;
  summary: boolean;
  fulltext: boolean;
  figures: boolean;
  references: boolean;
}

export function CacheStatus({ paper }: { paper: Paper }) {
  const [cache, setCache] = useState<CacheData | null>(null);
  const [ocrRunning, setOcrRunning] = useState(false);
  const [ocrError, setOcrError] = useState<string | null>(null);
  const [ocrProgress, setOcrProgress] = useState({ done: 0, total: 0 });
  const [embedRunning, setEmbedRunning] = useState(false);

  const enc = encodeURIComponent(paper.id);

  const reload = () => {
    fetch(`${API_BASE}/papers/${enc}/cache`)
      .then(r => r.ok ? r.json() : null).then(setCache).catch(() => {});
  };

  useEffect(() => {
    if (!paper) return;
    reload();
    const handler = (e: Event) => { if ((e as CustomEvent).detail.paperId === paper.id) reload(); };
    window.addEventListener("rs:paper-processed", handler);
    return () => window.removeEventListener("rs:paper-processed", handler);
  }, [paper?.id]);

  const f = paper.cache_flags || 0;
  const items = [
    { key: "PDF",        on: cache ? cache.pdf        : Boolean(f & 1) },
    { key: "Embeddings", on: cache ? cache.embeddings : Boolean(f & 2) },
    { key: "Summary",    on: cache ? cache.summary    : (Boolean(f & 4) || paper.status === "summarized") },
    { key: "Full text",  on: cache ? cache.fulltext   : Boolean(f & 32) },
    { key: "Figures",    on: cache ? cache.figures    : Boolean(f & 8) },
    { key: "References", on: cache ? cache.references : Boolean(f & 16) },
  ];
  const hasPdf = cache ? cache.pdf : Boolean(f & 1);

  const triggerOcr = () => {
    setOcrRunning(true);
    setOcrError(null);
    setOcrProgress({ done: 0, total: 0 });
    fetch(`${API_BASE}/papers/${enc}/ocr`, { method: "POST" })
      .then(r => {
        if (!r.ok) {
          return r.json().then(body => {
            setOcrRunning(false);
            setOcrError(body?.detail || `OCR failed (${r.status})`);
          }).catch(() => {
            setOcrRunning(false);
            setOcrError(`OCR failed (${r.status})`);
          });
        }
        let ticks = 0;
        const cachePoll = setInterval(() => {
          ticks++;
          fetch(`${API_BASE}/papers/${enc}/cache`)
            .then(r => r.ok ? r.json() : null)
            .then((c: CacheData | null) => {
              if (c) setCache(c);
              if (c && c.fulltext) {
                clearInterval(cachePoll);
                clearInterval(progPoll);
                setOcrRunning(false);
                window.dispatchEvent(new CustomEvent("rs:ocr-complete", { detail: { paperId: paper.id } }));
                window.dispatchEvent(new CustomEvent("rs:paper-processed", { detail: { paperId: paper.id } }));
              }
            }).catch(() => {});
          if (ticks > 120) { clearInterval(cachePoll); clearInterval(progPoll); setOcrRunning(false); }
        }, 5000);
        const progPoll = setInterval(() => {
          fetch(`${API_BASE}/papers/${enc}/ocr/progress`)
            .then(r => r.ok ? r.json() : null)
            .then((p: { pages_done: number; pages_total: number } | null) => {
              if (!p) return;
              setOcrProgress({ done: p.pages_done, total: p.pages_total });
              window.dispatchEvent(new CustomEvent("rs:ocr-progress", {
                detail: { paperId: paper.id, pagesDone: p.pages_done, pagesTotal: p.pages_total },
              }));
            }).catch(() => {});
        }, 2000);
      })
      .catch(() => { setOcrRunning(false); setOcrError("Network error"); });
  };

  return (
    <div className="cache-list">
      {items.map(i => (
        <div key={i.key} className={"cache-row " + (i.on ? "on" : "off")}>
          <span className="cbox">{i.on ? "✓" : "×"}</span>
          <span className="ckey">{i.key}</span>
          <span className="cwhen">{i.on ? "cached" : "missing"}</span>
        </div>
      ))}
      <div className="cache-actions">
        <button className="ghost" onClick={() => fetch(`${API_BASE}/papers/${enc}/ingest`, { method: "POST" })}>↻ Re-ingest</button>
        <button className="ghost" disabled={embedRunning} onClick={() => {
          setEmbedRunning(true);
          fetch(`${API_BASE}/papers/${enc}/embed`, { method: "POST" }).then(() => {
            let ticks = 0;
            const poll = setInterval(() => {
              ticks++;
              fetch(`${API_BASE}/papers/${enc}/cache`)
                .then(r => r.ok ? r.json() : null)
                .then((c: CacheData | null) => {
                  if (c) setCache(c);
                  if (c?.embeddings || ticks > 60) { clearInterval(poll); setEmbedRunning(false); }
                }).catch(() => {});
            }, 2000);
          }).catch(() => setEmbedRunning(false));
        }}>{embedRunning ? "↺ Embedding…" : "↺ Re-embed"}</button>
        {hasPdf && (
          <button className="ghost" disabled={ocrRunning} onClick={triggerOcr}
                  title="Run vision-LLM OCR on the PDF pages">
            {ocrRunning
              ? (ocrProgress.total > 0 ? `⌖ ${ocrProgress.done}/${ocrProgress.total} pages` : "⌖ OCR…")
              : "⌖ OCR"}
          </button>
        )}
        <button className="ghost" onClick={() =>
          fetch(`${API_BASE}/papers/${enc}/cache`, { method: "DELETE" }).then(() => {
            setCache({ pdf: false, embeddings: false, summary: false, fulltext: false, figures: false, references: false });
            window.dispatchEvent(new CustomEvent("rs:refreshPapers"));
          })
        }>✕ Evict</button>
      </div>
      {ocrError && <div style={{ padding: "6px 0 2px", fontSize: 11, color: "var(--rust)" }}>⚠ {ocrError}</div>}
    </div>
  );
}

// ── CitationVelocity ──────────────────────────────────────────────────────────

export function CitationVelocity({ paper }: { paper: Paper | null }) {
  const [vel, setVel] = useState<{ velocity_12w?: number[]; cited_by?: number; cites_delta?: number } | null>(null);
  const [w, setW] = useState(1);
  useEffect(() => {
    if (!paper) return;
    setVel(null);
    api.fetchVelocity(paper.id).then(raw => { const d = raw as typeof vel; d && setVel(d); }).catch(() => {});
  }, [paper?.id]);

  const windows = [
    { label: "4w",  n: 4 },
    { label: "8w",  n: 8 },
    { label: "12w", n: 12 },
    { label: "All", n: Infinity },
  ];
  const raw = vel?.velocity_12w || [];
  const pts = windows[w].n === Infinity ? raw : raw.slice(-windows[w].n);
  const max = Math.max(...pts, 1);
  const citedBy = vel?.cited_by ?? 0;
  const delta = vel?.cites_delta ?? 0;
  const allZero = pts.length > 0 && pts.every(v => v === 0);

  return (
    <div className="velocity">
      <div className="vel-head">
        <div className="vel-num">
          {citedBy > 0 ? citedBy : `+${delta}`}
          <span>{citedBy > 0 && delta === 0 ? "total cites" : "cites"}</span>
        </div>
        <div className="vel-tabs">
          {windows.map((win, i) => (
            <button key={i} className={"vel-tab " + (w === i ? "on" : "")} onClick={() => setW(i)}>{win.label}</button>
          ))}
        </div>
      </div>
      <div className="vel-bars">
        {pts.length > 0 && !allZero
          ? pts.map((v, i) => <div key={i} className="vel-bar" style={{ height: `${(v / max) * 100}%` }} />)
          : <div style={{ padding: "8px 0", color: "var(--ink-4)", fontSize: 11 }}>
              {citedBy > 0 ? `${citedBy} total citations — weekly breakdown below annual resolution` : "No citation data yet"}
            </div>
        }
      </div>
    </div>
  );
}

// ── ExportPanel ───────────────────────────────────────────────────────────────

export function ExportPanel({ paper }: { paper: Paper | null }) {
  const [downloading, setDownloading] = useState<string | null>(null);
  if (!paper) return null;

  const dl = (fmt: string) => {
    if (downloading) return;
    setDownloading(fmt);
    const url = api.exportUrl(paper.id, fmt);
    fetch(url)
      .then(r => {
        if (!r.ok) throw new Error("Export failed");
        return fmt === "json" ? r.json().then((d: unknown) => JSON.stringify(d, null, 2)) : r.text();
      })
      .then(text => {
        const ext = fmt === "obsidian" ? "md" : fmt;
        const blob = new Blob([text], { type: "text/plain" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `${paper.id.replace(/[:/]/g, "_")}.${ext}`;
        a.click();
        URL.revokeObjectURL(a.href);
      })
      .catch(console.error)
      .finally(() => setDownloading(null));
  };

  const options = [
    { k: "BibTeX",   fmt: "bib",      d: ".bib citation" },
    { k: "Markdown", fmt: "md",       d: "Reader view + notes" },
    { k: "Obsidian", fmt: "obsidian", d: "Vault note + links" },
    { k: "JSON",     fmt: "json",     d: "Raw metadata + trace" },
  ];
  return (
    <div className="export-list">
      {options.map(o => (
        <button key={o.k} className="export-row" onClick={() => dl(o.fmt)} disabled={!!downloading}>
          <span className="ex-k">{o.k}</span>
          <span className="ex-d">{downloading === o.fmt ? "Downloading…" : o.d}</span>
          <span className="ex-go">↓</span>
        </button>
      ))}
    </div>
  );
}

// ── MetaBlock ─────────────────────────────────────────────────────────────────

function MetaRow({ label, value, last, statusColor }: {
  label: string; value?: string | number | null; last?: boolean; statusColor?: string;
}) {
  if (value == null || value === "") return null;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "90px 1fr", gap: 8, padding: "6px 0", borderBottom: last ? "none" : "1px solid var(--rule)" }}>
      <span style={{ color: "var(--ink-4)" }}>{label}</span>
      <span style={{ color: statusColor || undefined, wordBreak: "break-all" }}>{value}</span>
    </div>
  );
}

function MetaBlock({ paper }: { paper: Paper | null }) {
  if (!paper) return null;
  const statusColor = paper.status === "summarized" ? "var(--ok)" : paper.status === "paywalled" ? "var(--rust)" : "var(--ember)";
  const arxivId = paper.id?.startsWith("arxiv:") ? paper.id.slice(6) : null;
  return (
    <div style={{ padding: "4px 18px 14px", fontSize: 12 }}>
      <MetaRow label="Venue"    value={paper.venue} />
      <MetaRow label="Published" value={paper.date} />
      <MetaRow label="Source"   value={paper.source} />
      <MetaRow label="Status"   value={paper.status} statusColor={statusColor} />
      <MetaRow label="arXiv ID" value={arxivId} />
      <MetaRow label="Topics"   value={paper.topics?.join(", ")} last={!paper.authors?.length} />
      {paper.authors && paper.authors.length > 0 && (
        <div style={{ padding: "6px 0", borderTop: "1px solid var(--rule)" }}>
          <div style={{ color: "var(--ink-4)", marginBottom: 4 }}>Authors</div>
          <div style={{ color: "var(--ink-3)", lineHeight: 1.6 }}>{paper.authors.join(", ")}</div>
        </div>
      )}
      <div style={{ display: "flex", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
        {arxivId && (
          <a href={"https://arxiv.org/abs/" + arxivId} target="_blank" rel="noopener noreferrer"
             style={{ fontSize: 11, color: "var(--rust)", textDecoration: "none" }}>↗ arXiv</a>
        )}
      </div>
    </div>
  );
}

// ── RightRail ─────────────────────────────────────────────────────────────────

interface RightRailProps {
  paper: Paper | null;
  trace?: TraceItem[] | null;
  mentions?: Mention[] | null;
}

export function RightRail({ paper, trace, mentions }: RightRailProps) {
  if (!paper) return (
    <div className="pane right">
      <div className="section-head">No selection</div>
    </div>
  );

  return (
    <div className="pane right">
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
            <div className="v">{paper.scores.relevance}</div>
            <div className="k">Relevance</div>
          </div>
          <div className="stat-card">
            <div className="v">{paper.scores.novelty}</div>
            <div className="k">Novelty</div>
          </div>
          <div className="stat-card">
            <div className="v">{paper.scores.velocity}</div>
            <div className="k">Velocity</div>
          </div>
        </div>

        <Accordion title="Reasoning trace" count={trace?.length || 0} defaultOpen>
          <TraceView traces={trace} />
        </Accordion>

        <Accordion title="Citation velocity" defaultOpen>
          <CitationVelocity paper={paper} />
        </Accordion>

        <Accordion title="Cache status">
          <CacheStatus paper={paper} />
        </Accordion>

        <Accordion title="Export">
          <ExportPanel paper={paper} />
        </Accordion>

        <Accordion title="Embedding neighborhood">
          <EmbeddingsMini paper={paper} />
        </Accordion>

        <Accordion title="Web mentions" count={mentions?.length || 0}>
          <Mentions mentions={mentions} paper={paper} />
        </Accordion>

        <Accordion title="Metadata">
          <MetaBlock paper={paper} />
        </Accordion>
      </div>
    </div>
  );
}
