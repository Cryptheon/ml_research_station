import { useState, useMemo, useEffect, useRef } from "react";
import { api, API_BASE } from "../api";
import type { Paper, IngestRun } from "../types";
import { VelocitySpark } from "./Features";

const PAGE_SIZE = 50;

function _relTime(iso: string): string {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 120) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return m > 0 ? `${h}h ${m}m ago` : `${h}h ago`;
  }
  return `${Math.floor(s / 86400)}d ago`;
}

// ── IngestionPanel ────────────────────────────────────────────────────────────

interface IngestionPanelProps {
  onPull: (interests: string[]) => void;
  pulling: boolean;
  onOpenIngest: () => void;
  onSelectRun: (run: IngestRun | null) => void;
  selectedRun: IngestRun | null;
}

function IngestionPanel({ onPull, pulling, onOpenIngest, onSelectRun, selectedRun }: IngestionPanelProps) {
  const [runs, setRuns] = useState<IngestRun[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    fetch(API_BASE + "/users/me/ingests?limit=3")
      .then(r => r.ok ? r.json() : [])
      .then(data => { if (data && data.length) setRuns(data); })
      .catch(() => {});
  }, [pulling]);

  const lastInterests = runs.length ? runs[0].interests : [];
  const display = lastInterests.length ? lastInterests : ["computer vision", "emergence of intelligence"];

  const handleChipClick = (run: IngestRun) => {
    if (expandedId !== run.id) {
      setExpandedId(run.id); onSelectRun(null);
    } else if (!selectedRun || selectedRun.id !== run.id) {
      onSelectRun(run);
    } else {
      setExpandedId(null); onSelectRun(null);
    }
  };

  return (
    <div className="ingest">
      <button className="pull" onClick={() => onPull(lastInterests)} disabled={pulling}>
        {pulling ? "Ingesting…" : <><span>Pull new papers</span> <span className="pull-hint">· {display.length} interest{display.length === 1 ? "" : "s"}</span></>}
        {pulling && <span className="sweep" />}
      </button>

      {runs.length > 0 ? (
        <div className="ip-runs">
          {runs.map(run => {
            const isExpanded = expandedId === run.id;
            const isSelected = selectedRun?.id === run.id;
            return (
              <div key={run.id} className={"ip-run " + (isSelected ? "selected " : "") + (isExpanded ? "expanded" : "")}>
                <div className="ip-chips" onClick={() => handleChipClick(run)}>
                  {(run.interests || []).slice(0, 4).map(t => (
                    <span key={t} className={"ip-chip " + (isSelected ? "on" : "")}>{t}</span>
                  ))}
                  {(run.interests || []).length > 4 && <span className="ip-more">+{run.interests.length - 4}</span>}
                  <span className="ip-chev">{isExpanded ? "▾" : "›"}</span>
                </div>
                {isExpanded && (
                  <div className="ip-meta">
                    <span className="ip-meta-stat"><b>{run.found}</b> papers found</span>
                    <span className="ip-meta-sep">·</span>
                    <span className="ip-meta-stat">{run.scanned} scanned</span>
                    <span className="ip-meta-sep">·</span>
                    <span className="ip-meta-stat">{_relTime(run.ran_at)}</span>
                    <div className="ip-meta-hint">{isSelected ? "Showing in queue ↑" : "Click chips again to show papers →"}</div>
                    <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                      <button className="ip-edit" style={{ color: "var(--rust)", fontSize: 10 }}
                        onClick={e => {
                          e.stopPropagation();
                          fetch(`${API_BASE}/users/me/ingests/${run.id}`, { method: "DELETE" }).catch(() => {});
                          setRuns(prev => prev.filter(r => r.id !== run.id));
                          if (isSelected) onSelectRun(null);
                          setExpandedId(null);
                        }}>✕ Remove</button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="interests-preview">
          {display.slice(0, 3).map(t => <span key={t} className="ip-chip">{t}</span>)}
          {display.length > 3 && <span className="ip-more">+{display.length - 3}</span>}
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 0 2px" }}>
        <button className="ip-edit" onClick={onOpenIngest}>Edit interests…</button>
        {selectedRun && (
          <button className="ip-edit" style={{ color: "var(--rust)" }} onClick={() => { onSelectRun(null); setExpandedId(null); }}>✕ Clear filter</button>
        )}
      </div>
    </div>
  );
}

// ── FilterDrawer ──────────────────────────────────────────────────────────────

interface Filters {
  relevance: number;
  days: number;
  topics: string[];
}

function FilterDrawer({ filters, setFilters }: { filters: Filters; setFilters: (f: Filters) => void }) {
  const topics = ["LLM", "Bio", "Interpretability", "RL", "Generative", "Geometric", "Efficiency"];
  const onSliderClick = (key: keyof Filters, max: number, min = 0) => (e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const v = Math.max(min, Math.min(max, ((e.clientX - r.left) / r.width) * (max - min) + min));
    setFilters({ ...filters, [key]: key === "days" ? Math.round(v) : v });
  };
  return (
    <div className="filter-drawer">
      <div className="group">
        <div className="glabel">
          <span>Minimum relevance</span>
          <span className="val">{filters.relevance.toFixed(2)}</span>
        </div>
        <div className="slider" onClick={onSliderClick("relevance", 1)}>
          <div className="track" /><div className="fill" style={{ width: `${filters.relevance * 100}%` }} />
          <div className="knob" style={{ left: `${filters.relevance * 100}%` }} />
        </div>
      </div>
      <div className="group">
        <div className="glabel">
          <span>Time window</span><span className="val">{filters.days} days</span>
        </div>
        <div className="slider" onClick={onSliderClick("days", 1825, 7)}>
          <div className="track" /><div className="fill" style={{ width: `${(filters.days / 1825) * 100}%` }} />
          <div className="knob" style={{ left: `${(filters.days / 1825) * 100}%` }} />
        </div>
      </div>
      <div className="group">
        <div className="glabel">
          <span>Topics</span><span className="val">{filters.topics.length || "all"}</span>
        </div>
        <div className="chips">
          {topics.map(t => (
            <button key={t} className={"src " + (filters.topics.includes(t) ? "on" : "")}
              onClick={() => {
                const has = filters.topics.includes(t);
                setFilters({ ...filters, topics: has ? filters.topics.filter(x => x !== t) : [...filters.topics, t] });
              }}>{t}</button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── PaperItem ─────────────────────────────────────────────────────────────────

const CACHE_PDF = 1, CACHE_EMB = 2, CACHE_SUM = 4;

function _sourceTag(p: Paper): { label: string; color: string } {
  if (p.id.startsWith("web:"))       return { label: "WEB",     color: "#7b9fd4" };
  if (p.id.startsWith("wikipedia:")) return { label: "WIKI",    color: "#4db6ac" };
  const src = (p.source || "").toLowerCase();
  if (src === "biorxiv")   return { label: "BIORXIV", color: "#a8c97a" };
  if (src === "pubmed")    return { label: "PUBMED",  color: "#e07060" };
  if (src === "openreview") return { label: "OPENREV", color: "#b39ddb" };
  return { label: "ARXIV", color: "var(--ink-4)" };
}

interface PaperItemProps {
  p: Paper;
  active: boolean;
  expanded: boolean;
  onSelect: (id: string) => void;
  onExpand: (id: string) => void;
}

function PaperItem({ p, active, expanded, onSelect, onExpand }: PaperItemProps) {
  const [summarising, setSummarising] = useState(false);

  const f = p.cache_flags || 0;
  const cache = {
    pdf: Boolean(f & CACHE_PDF),
    emb: Boolean(f & CACHE_EMB),
    sum: Boolean(f & CACHE_SUM) || p.status === "summarized",
  };
  const vel12w = p.velocity_12w || [];
  const recentDelta = vel12w.length ? vel12w[vel12w.length - 1] : null;

  const summarise = (e: React.MouseEvent) => {
    e.stopPropagation();
    setSummarising(true);
    api.triggerSummarise(p.id)
      .then(() => setTimeout(() => setSummarising(false), 3000))
      .catch(() => setSummarising(false));
  };

  const tag = _sourceTag(p);

  return (
    <div className={"paper " + (active ? "active " : "") + (expanded ? "" : "collapsed")} onClick={() => { onSelect(p.id); onExpand(p.id); }}>
      <div className="row1">
        <span className="src-tag" style={{ color: tag.color, borderColor: tag.color }}>{tag.label}</span>
        <span className="venue">{p.venue}</span>
        <span>·</span>
        <span className="date">{p.date.slice(0, 7)}</span>
        {p.pinned && <span className="pin" title="Pinned">◆</span>}
        <span className="grow" />
        <span className="cache-dots" title={`PDF ${cache.pdf ? "✓" : "×"} · Emb ${cache.emb ? "✓" : "×"} · Sum ${cache.sum ? "✓" : "×"}`}>
          <span className={"cd " + (cache.pdf ? "on" : "")} />
          <span className={"cd " + (cache.emb ? "on" : "")} />
          <span className={"cd " + (cache.sum ? "on" : "")} />
        </span>
      </div>
      <div className="title">{p.title}</div>
      <div className="paper-spark">
        <VelocitySpark data={vel12w} />
        <span className="spark-label">12w cites</span>
        {recentDelta !== null && <span className="spark-val">+{recentDelta}</span>}
      </div>
      {expanded && (
        <div className="paper-detail" onClick={e => e.stopPropagation()}>
          <div className="authors">{p.authors.join(", ")}</div>
          <div className="scores">
            <div className="score rel">
              <span className="v">{(p.scores.relevance * 100).toFixed(0)}</span>REL
              <div className="bar"><i style={{ width: `${p.scores.relevance * 100}%` }} /></div>
            </div>
            <div className="score nov">
              <span className="v">{(p.scores.novelty * 100).toFixed(0)}</span>NOV
              <div className="bar"><i style={{ width: `${p.scores.novelty * 100}%` }} /></div>
            </div>
            <div className="score vel">
              <span className="v">{(p.scores.velocity * 100).toFixed(0)}</span>VEL
              <div className="bar"><i style={{ width: `${p.scores.velocity * 100}%` }} /></div>
            </div>
          </div>
          <div className="mini-actions">
            <button onClick={() => { if (!p.id.startsWith("wikipedia:")) window.open(api.pdfUrl(p.id)); }}
              disabled={p.id.startsWith("wikipedia:")} title={p.id.startsWith("wikipedia:") ? "No PDF" : "Open PDF"}>PDF</button>
            <button onClick={() => api.togglePin(p.id)}>Pin</button>
            <button onClick={summarise} disabled={summarising} style={{ color: cache.sum ? "var(--ink-4)" : "var(--rust)" }}>
              {summarising ? "Running…" : cache.sum ? "Re-sum" : "Summarise"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── LeftRail ──────────────────────────────────────────────────────────────────

interface LeftRailProps {
  activeId: string | null;
  onSelect: (id: string) => void;
  onPull: (interests: string[]) => void;
  pulling: boolean;
  onOpenIngest: () => void;
  selectedRun: IngestRun | null;
  onSelectRun: (run: IngestRun | null) => void;
}

export function LeftRail({ activeId, onSelect, onPull, pulling, onOpenIngest, selectedRun, onSelectRun }: LeftRailProps) {
  const [filtOpen, setFiltOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(activeId);
  const [typeFilter, setTypeFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<Filters>({ relevance: 0, days: 1825, topics: [] });
  const [page, setPage] = useState(0);
  const [railPapers, setRailPapers] = useState<Paper[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const [manualIds, setManualIds] = useState<Set<string> | null>(null);

  const filterSig = [query, typeFilter, filters.relevance, filters.days, ...filters.topics, selectedRun?.id || ""].join("|");
  const prevSigRef = useRef(filterSig);
  if (prevSigRef.current !== filterSig) {
    prevSigRef.current = filterSig;
    if (page !== 0) setPage(0);
  }

  useEffect(() => {
    if (typeFilter === "manual" && manualIds === null) {
      api.fetchManualPaperIds()
        .then((ids: string[]) => setManualIds(new Set(ids)))
        .catch(() => setManualIds(new Set()));
    }
  }, [typeFilter]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const params = new URLSearchParams();
    const isSpecialMode = selectedRun || typeFilter === "manual";

    if (isSpecialMode) {
      params.set("limit", "500");
      params.set("offset", "0");
      params.set("sort", "date");
      if (selectedRun?.ran_at) {
        const daysAgo = Math.ceil((Date.now() - new Date(selectedRun.ran_at).getTime()) / 86400000) + 2;
        params.set("since_days", String(Math.min(daysAgo, 365)));
      }
    } else {
      params.set("limit", String(PAGE_SIZE));
      params.set("offset", String(page * PAGE_SIZE));
      if (query) params.set("q", query);
      if (filters.days < 1825) params.set("since_days", String(filters.days));
      if (filters.topics.length) filters.topics.forEach(t => params.append("topics[]", t));
      if (typeFilter === "wiki") params.append("sources[]", "wikipedia");
      else if (typeFilter === "web") params.append("sources[]", "web");
      else if (typeFilter === "paper") ["arxiv", "biorxiv", "openreview", "semantic_scholar"].forEach(s => params.append("sources[]", s));
    }

    fetch(`${API_BASE}/papers/queue?${params}`)
      .then(r => r.ok ? r.json() : [])
      .then((data: Paper[]) => {
        if (cancelled) return;
        setRailPapers(data);
        setHasMore(!isSpecialMode && data.length === PAGE_SIZE);
        setLoading(false);
      })
      .catch(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [query, filters.days, filters.topics, typeFilter, page, selectedRun, refreshTick]);

  useEffect(() => {
    const handler = () => { setPage(0); setRefreshTick(t => t + 1); };
    window.addEventListener("rs:papers-updated", handler);
    window.addEventListener("rs:manual-paper-added", handler);
    window.addEventListener("rs:refreshPapers", handler);
    return () => {
      window.removeEventListener("rs:papers-updated", handler);
      window.removeEventListener("rs:manual-paper-added", handler);
      window.removeEventListener("rs:refreshPapers", handler);
    };
  }, []);

  useEffect(() => {
    const handler = (e: Event) => {
      const { paperId } = (e as CustomEvent).detail;
      fetch(`${API_BASE}/papers/${encodeURIComponent(paperId)}/cache`)
        .then(r => r.ok ? r.json() : null)
        .then((c: Record<string, boolean> | null) => {
          if (!c) return;
          const flags = (c.pdf ? 1 : 0) | (c.embeddings ? 2 : 0) | (c.summary ? 4 : 0) |
                        (c.figures ? 8 : 0) | (c.references ? 16 : 0) | (c.fulltext ? 32 : 0);
          setRailPapers(prev => prev.map(p =>
            p.id === paperId
              ? { ...p, cache_flags: flags, status: c.summary && p.status !== "paywalled" ? "summarized" : p.status }
              : p,
          ));
        }).catch(() => {});
    };
    window.addEventListener("rs:paper-processed", handler);
    return () => window.removeEventListener("rs:paper-processed", handler);
  }, []);

  const displayPapers = useMemo(() => {
    let list = railPapers;
    if (selectedRun) {
      if (selectedRun.paper_ids && selectedRun.paper_ids.length > 0) {
        const idSet = new Set(selectedRun.paper_ids);
        list = list.filter(p => idSet.has(p.id));
      } else if (selectedRun.found === 0) {
        list = [];
      } else {
        const start = new Date(selectedRun.ran_at).getTime() - 5000;
        const end = start + 5000 + (selectedRun.duration_seconds || 120) * 1000 + 30000;
        const byWindow = list.filter(p => {
          if (!p.created_at) return false;
          const t = new Date(p.created_at).getTime();
          return t >= start && t <= end;
        });
        if (byWindow.length > 0) {
          list = byWindow;
        } else if (selectedRun.interests?.length) {
          const terms = selectedRun.interests.flatMap(i => {
            const full = i.toLowerCase();
            const words = full.trim().split(/\s+/);
            const truncated = words.slice(0, 2).join(" ");
            return full === truncated ? [full] : [full, truncated];
          });
          list = list.filter(p => terms.some(t => p.title.toLowerCase().includes(t) || (p.abstract || "").toLowerCase().includes(t)));
        } else {
          list = [];
        }
      }
    }
    if (typeFilter === "manual" && manualIds) list = list.filter(p => manualIds.has(p.id));
    if (filters.relevance > 0) list = list.filter(p => p.scores.relevance >= filters.relevance);
    return list;
  }, [railPapers, selectedRun, typeFilter, manualIds, filters.relevance]);

  const toggleExpand = (id: string) => setExpanded(expanded === id ? null : id);
  const activeFilters = (filters.topics.length ? 1 : 0) + (filters.relevance > 0 ? 1 : 0) + (filters.days < 1825 ? 1 : 0);
  const isSpecialMode = selectedRun || typeFilter === "manual";

  const countLabel = isSpecialMode
    ? `${displayPapers.length}`
    : railPapers.length === 0 && !loading ? "0"
    : `${page * PAGE_SIZE + 1}–${page * PAGE_SIZE + railPapers.length}`;

  return (
    <div className="pane left">
      <IngestionPanel onPull={onPull} pulling={pulling} onOpenIngest={onOpenIngest} onSelectRun={onSelectRun} selectedRun={selectedRun} />

      {selectedRun && (
        <div style={{ margin: "0 8px 4px", padding: "7px 10px", background: "var(--bg-2)", border: "1px solid var(--rule)", borderRadius: 5, fontSize: 11, display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ flex: 1, color: "var(--ink-3)" }}>
            <b style={{ color: "var(--ink)" }}>{displayPapers.length} papers</b> from pull: {selectedRun.interests.slice(0, 2).join(", ")}{selectedRun.interests.length > 2 ? ` +${selectedRun.interests.length - 2}` : ""}
          </span>
          <button style={{ fontSize: 11, background: "none", border: "none", cursor: "pointer", color: "var(--ink-4)", padding: "2px 4px" }} onClick={() => onSelectRun(null)}>✕ Clear</button>
        </div>
      )}

      <div className="search">
        <input placeholder="Search title, author, ID…" value={query} onChange={e => setQuery(e.target.value)} />
      </div>
      <div className="type-filter-bar">
        {[
          { key: "all",    label: "All" },
          { key: "paper",  label: "Papers" },
          { key: "wiki",   label: "Wiki",   color: "#4db6ac" },
          { key: "web",    label: "Web",    color: "#7b9fd4" },
          { key: "manual", label: "Manual", color: "var(--sulfur)" },
        ].map(opt => (
          <button key={opt.key} className={"tfbtn " + (typeFilter === opt.key ? "on" : "")}
            onClick={() => setTypeFilter(opt.key)}
            style={typeFilter === opt.key && opt.color ? { color: opt.color, borderColor: opt.color } : {}}>
            {opt.label}
          </button>
        ))}
      </div>
      <div className="filters-bar">
        <button className={"fbtn " + (filtOpen ? "on" : "")} onClick={() => setFiltOpen(!filtOpen)}>
          Filters{activeFilters ? ` · ${activeFilters}` : ""} <span className="chev">{filtOpen ? "▾" : "▸"}</span>
        </button>
        <button className="fbtn">Sort: relevance <span className="chev">▾</span></button>
      </div>
      {filtOpen && <FilterDrawer filters={filters} setFilters={setFilters} />}
      <div className="section-head">
        {selectedRun ? "Pull" : "Papers"}
        <span className="count">{loading ? "…" : countLabel}</span>
      </div>
      <div className="queue">
        {displayPapers.map(p => (
          <PaperItem key={p.id} p={p} active={p.id === activeId} expanded={expanded === p.id} onSelect={onSelect} onExpand={toggleExpand} />
        ))}
        {displayPapers.length === 0 && !loading && (
          <div className="rail-empty">
            <div className="rail-empty-glyph">⌕</div>
            <div className="rail-empty-title">{selectedRun ? "No papers from this pull" : "No matches"}</div>
            <div className="rail-empty-hint">
              {selectedRun ? "Papers ingested in this run may have been added before tracking was enabled."
                : query ? <>Searched for <b>"{query}"</b>.</> : "Try loosening filters."}
            </div>
            {selectedRun
              ? <button className="rail-empty-action" onClick={() => onSelectRun(null)}>Show all papers</button>
              : <button className="rail-empty-action" onClick={() => { setQuery(""); setFilters({ ...filters, relevance: 0, topics: [] }); }}>Reset filters</button>
            }
          </div>
        )}
        {loading && railPapers.length === 0 && (
          <div className="rail-empty" style={{ opacity: 0.5 }}>
            <div className="rail-empty-glyph" style={{ fontSize: 18 }}>⟳</div>
            <div className="rail-empty-hint">Loading…</div>
          </div>
        )}
      </div>
      {!isSpecialMode && (
        <div className="rail-page-controls">
          <button className="rpc-btn" disabled={page === 0 || loading} onClick={() => setPage(p => p - 1)}>← Prev</button>
          <span className="rpc-page">p.{page + 1}</span>
          <button className="rpc-btn" disabled={!hasMore || loading} onClick={() => setPage(p => p + 1)}>Next →</button>
        </div>
      )}
    </div>
  );
}
