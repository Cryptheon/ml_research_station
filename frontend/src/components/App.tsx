import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { api, API_BASE } from "../api";
import type { Paper, Citation, Health, BatchStatus, Traversal, ProcessingItem, IngestRun } from "../types";
import { LeftRail } from "./LeftRail";
import { CenterStage } from "./CenterStage";
import { ContextRail } from "./AgentPanel";
import type { TraversalResult } from "./AgentPanel";
import { LibraryPage } from "./Library";
import { PromptsPage } from "./Prompts";
import { RoutingMenu, AgentWatch } from "./Features";
import { UsageModal } from "./UsageModal";
import { ApiConfigModal } from "./ApiConfigModal";
import { IngestModal, IngestResultPanel } from "./IngestModal";
import type { IngestResult } from "./IngestModal";
import { BottomChat } from "./BottomChat";

// Module-level caches for per-paper trace and web-mention data
const _traces: Record<string, unknown[]> = {};
const _webMentions: Record<string, unknown> = {};

const TWEAK_DEFAULTS = {
  accent: "rust",
  density: "comfortable",
  showChat: true,
};

interface Toast { type: "warn" | "info"; msg: string; }
interface Sources { arXiv: boolean; bioRxiv: boolean; PubMed: boolean; OpenReview: boolean; Wikipedia: boolean; }
interface Tweaks { accent: string; density: string; showChat: boolean; }

export function App() {
  const [page, setPage] = useState("explorer");
  const [activeId, setActiveId] = useState("arxiv:2504.11823");
  const [mode, setMode] = useState("read");
  const [pulling, setPulling] = useState(false);
  const [ingestResult, setIngestResult] = useState<IngestResult | null>(null);
  const _ingestPapersRef = useRef<Paper[]>([]);
  const [toast, setToast] = useState<Toast | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = (type: Toast["type"], msg: string) => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    setToast({ type, msg });
    toastTimerRef.current = setTimeout(() => setToast(null), 6000);
  };
  void showToast;

  const [sources, setSources] = useState<Sources>({ arXiv: true, bioRxiv: false, PubMed: false, OpenReview: false, Wikipedia: false });
  const [chatOpen, setChatOpen] = useState(false);
  const [chatHeight, setChatHeight] = useState(320);
  const [ingestOpen, setIngestOpen] = useState(false);
  const [tweaks, setTweaks] = useState<Tweaks>(TWEAK_DEFAULTS);
  const [editModeActive, setEditModeActive] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const [routing, setRouting] = useState("local");
  const [routingOpen, setRoutingOpen] = useState(false);
  const [watchOpen, setWatchOpen] = useState(false);
  const [selectedRun, setSelectedRun] = useState<IngestRun | null>(null);
  const [apiConfigOpen, setApiConfigOpen] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);
  const [theme, setTheme] = useState("light");
  const [procItems, setProcItems] = useState<ProcessingItem[]>([]);
  const [batchStatus, setBatchStatus] = useState<BatchStatus | null>(null);
  const prevProcRef = useRef<ProcessingItem[]>([]);
  const batchWasRunning = useRef(false);

  const [papers, setPapers] = useState<Paper[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [trace, setTrace] = useState<unknown[]>([]);
  const [mentions, setMentions] = useState<unknown>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [apiReady, setApiReady] = useState(false);
  const [selectedTraversal, setSelectedTraversal] = useState<TraversalResult | null>(null);
  const traversalForGraph = useMemo(() =>
    selectedTraversal ? {
      root_id: selectedTraversal.start_id,
      nodes: selectedTraversal.nodes_visited,
      edges: selectedTraversal.edges_walked.map(e => ({ from: e.from_id, to: e.to_id })),
    } as Traversal : undefined,
  [selectedTraversal]);

  // ── Boot: listen for api.boot() to finish ─────────────────────────────
  useEffect(() => {
    const onLoaded = (e: Event) => {
      const { papers: loaded, health: h } = (e as CustomEvent<{ papers: Paper[]; health: Health | null }>).detail;
      if (loaded?.length) setPapers(loaded);
      setHealth(h);
      setApiReady(true);
    };
    document.addEventListener("rs:loaded", onLoaded);
    return () => document.removeEventListener("rs:loaded", onLoaded);
  }, []);

  // ── Open chat drawer from paper reader ────────────────────────────────
  useEffect(() => {
    const handler = () => setChatOpen(true);
    document.addEventListener("rs:open-chat", handler);
    return () => document.removeEventListener("rs:open-chat", handler);
  }, []);

  // ── Poll for processing items + batch status ──────────────────────────
  useEffect(() => {
    if (!apiReady) return;
    const poll = setInterval(async () => {
      const s = await api.fetchProcessingStatus().catch(() => ({ items: [] as ProcessingItem[] }));
      const items = s.items ?? [];
      const prev = prevProcRef.current;
      prev.filter(p => !items.some(c => c.paper_id === p.paper_id)).forEach(item => {
        window.dispatchEvent(new CustomEvent("rs:paper-processed", { detail: { paperId: item.paper_id } }));
      });
      prevProcRef.current = items;
      setProcItems(items);

      const bs = await api.fetchBatchStatus().catch(() => null);
      if (bs) {
        setBatchStatus(bs);
        if (batchWasRunning.current && !bs.running) {
          void refreshPapers();
          if (typeof window.refreshGraph === "function") window.refreshGraph();
        }
        batchWasRunning.current = bs.running;
      }
    }, 2500);
    return () => clearInterval(poll);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiReady]);

  // ── Update cache flags when a paper finishes processing ───────────────
  useEffect(() => {
    const handler = (e: Event) => {
      const { paperId } = (e as CustomEvent<{ paperId: string }>).detail;
      fetch(`${API_BASE}/papers/${encodeURIComponent(paperId)}/cache`)
        .then(r => r.ok ? r.json() : null)
        .then((c: Record<string, boolean> | null) => {
          if (!c) return;
          const flags = (c.pdf ? 1 : 0) | (c.embeddings ? 2 : 0) | (c.summary ? 4 : 0) |
                        (c.figures ? 8 : 0) | (c.references ? 16 : 0) | (c.fulltext ? 32 : 0);
          setPapers(prev => prev.map(p =>
            p.id === paperId
              ? { ...p, cache_flags: flags, status: c.summary && p.status !== "paywalled" ? "summarized" : p.status }
              : p
          ));
        }).catch(() => {});
    };
    window.addEventListener("rs:paper-processed", handler);
    return () => window.removeEventListener("rs:paper-processed", handler);
  }, []);

  // ── Fetch trace + citations when active paper changes ─────────────────
  useEffect(() => {
    if (!activeId || !apiReady) return;
    setSelectedTraversal(null);

    if (_traces[activeId]) {
      setTrace(_traces[activeId]);
    } else {
      api.fetchTrace(activeId).then(t => {
        const arr = t as unknown[];
        setTrace(arr);
        _traces[activeId] = arr;
      }).catch(() => setTrace([]));
    }

    if (_webMentions[activeId]) {
      setMentions(_webMentions[activeId]);
    } else {
      setMentions(null);
    }

    api.fetchCitations(activeId).then(graph => {
      setCitations(api.citationEdges(graph));
    }).catch(() => {});
  }, [activeId, apiReady]);

  // ── Refresh all papers from API ───────────────────────────────────────
  const refreshPapers = useCallback(async () => {
    try {
      const PAGE = 500;
      let all: Paper[] = [], offset = 0;
      while (true) {
        const pg = await api.fetchPapers({ limit: PAGE, offset });
        all = all.concat(pg);
        if (pg.length < PAGE) break;
        offset += PAGE;
      }
      setPapers(all);
      window.dispatchEvent(new CustomEvent("rs:papers-updated"));
    } catch (e) {
      console.warn("refresh failed", e);
    }
  }, []);

  // Expose refreshPapers via CustomEvent so child components can trigger it
  useEffect(() => {
    (window as Window & { refreshPapers?: () => Promise<void> }).refreshPapers = refreshPapers;
    const handler = () => void refreshPapers();
    window.addEventListener("rs:refreshPapers", handler);
    return () => window.removeEventListener("rs:refreshPapers", handler);
  }, [refreshPapers]);

  const onPull = useCallback(async (
    interests: string[],
    activeSources: string[] = [],
    dateParams: Record<string, unknown> = {},
    arxivCategories: string[] | null = null,
    biorxivCategories: string[] | null = null,
    _wikipediaLanguages: string[] | null = null,
  ) => {
    setPulling(true);
    try {
      const { job_id } = await api.startIngest({
        interests,
        sources: activeSources,
        window_days: (dateParams.window_days as number | undefined) ?? 14,
        date_from: (dateParams.date_from as string | null | undefined) ?? null,
        date_to: (dateParams.date_to as string | null | undefined) ?? null,
        arxiv_categories: arxivCategories,
        biorxiv_categories: biorxivCategories,
      });
      try { localStorage.setItem("mpe:ingestLast", JSON.stringify(interests)); } catch { /* ignore */ }

      _ingestPapersRef.current = [];
      setIngestResult({ running: true, found: 0, scanned: 0, duration_ms: 0, papers: [], errors: [] });
      api.openIngestSocket(job_id, {
        onPaper: (partial) => {
          _ingestPapersRef.current = [..._ingestPapersRef.current, partial];
          setPapers(prev => prev.some(p => p.id === partial.id) ? prev : [partial, ...prev]);
          setIngestResult(prev => prev ? { ...prev, papers: _ingestPapersRef.current.slice(0, 50) } : null);
        },
        onDone: async (frame) => {
          const stats = { when: "just now", found: frame.found, scanned: frame.scanned };
          try { localStorage.setItem("mpe:ingestStats", JSON.stringify(stats)); } catch { /* ignore */ }
          await refreshPapers();
          setPulling(false);
          setIngestResult({
            running: false,
            found: frame.found ?? 0,
            scanned: frame.scanned ?? 0,
            duration_ms: frame.duration_ms ?? 0,
            papers: _ingestPapersRef.current.slice(0, 50),
            errors: [],
          });
          _ingestPapersRef.current = [];
        },
        onError: async (msg) => {
          console.error("Ingest error:", msg);
          await refreshPapers();
          setPulling(false);
          setIngestResult({
            running: false,
            found: _ingestPapersRef.current.length,
            scanned: 0,
            duration_ms: 0,
            papers: _ingestPapersRef.current.slice(0, 50),
            errors: [msg],
          });
          _ingestPapersRef.current = [];
        },
      });
    } catch (err) {
      console.error("Failed to start ingest:", err);
      setPulling(false);
    }
  }, [refreshPapers]);

  // ── Theme ──────────────────────────────────────────────────────────────
  useEffect(() => {
    const tSaved = localStorage.getItem("mpe:theme");
    if (tSaved === "dark" || tSaved === "light") setTheme(tSaved);
  }, []);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("mpe:theme", theme);
  }, [theme]);

  // ── Persist UI state ───────────────────────────────────────────────────
  useEffect(() => {
    const saved = localStorage.getItem("mpe:activeId");
    if (saved && papers.some(p => p.id === saved)) setActiveId(saved);
    const mSaved = localStorage.getItem("mpe:mode");
    if (mSaved) setMode(mSaved);
    const pSaved = localStorage.getItem("mpe:page");
    if (pSaved) setPage(pSaved);
    const rSaved = localStorage.getItem("mpe:rightCollapsed");
    if (rSaved) setRightCollapsed(rSaved === "1");
    const rtSaved = localStorage.getItem("mpe:routing");
    if (rtSaved) setRouting(rtSaved);
    const chSaved = localStorage.getItem("mpe:chatHeight");
    if (chSaved) setChatHeight(parseInt(chSaved, 10) || 320);
  }, [papers]);
  useEffect(() => { localStorage.setItem("mpe:activeId", activeId); }, [activeId]);
  useEffect(() => { if (activeId && apiReady) api.recordPaperView(activeId, "user"); }, [activeId, apiReady]);
  useEffect(() => { localStorage.setItem("mpe:mode", mode); }, [mode]);
  useEffect(() => { localStorage.setItem("mpe:page", page); }, [page]);
  useEffect(() => { localStorage.setItem("mpe:rightCollapsed", rightCollapsed ? "1" : "0"); }, [rightCollapsed]);
  useEffect(() => { localStorage.setItem("mpe:routing", routing); }, [routing]);
  useEffect(() => { localStorage.setItem("mpe:chatHeight", String(chatHeight)); }, [chatHeight]);

  // ── Keyboard shortcuts ─────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "`") {
        e.preventDefault();
        setChatOpen(v => !v);
      }
      if (e.key === "Escape" && ingestOpen) setIngestOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ingestOpen]);

  useEffect(() => {
    const handler = (e: MessageEvent<{ type?: string }>) => {
      if (!e.data || typeof e.data !== "object") return;
      if (e.data.type === "__activate_edit_mode") setEditModeActive(true);
      if (e.data.type === "__deactivate_edit_mode") setEditModeActive(false);
    };
    window.addEventListener("message", handler);
    window.parent.postMessage({ type: "__edit_mode_available" }, "*");
    return () => window.removeEventListener("message", handler);
  }, []);

  const updateTweak = (key: keyof Tweaks, value: string | boolean) => {
    const next = { ...tweaks, [key]: value };
    setTweaks(next);
    window.parent.postMessage({ type: "__edit_mode_set_keys", edits: { [key]: value } }, "*");
  };

  useEffect(() => {
    const accentMap: Record<string, string> = { rust: "#A23E22", ember: "#C75F30", sulfur: "#B0853A", clay: "#7A4E33" };
    if (accentMap[tweaks.accent]) {
      document.documentElement.style.setProperty("--rust", accentMap[tweaks.accent]);
    }
  }, [tweaks.accent]);

  const toggleSource = (s: string) => setSources(prev => ({ ...prev, [s]: !prev[s as keyof Sources] }));

  const paper = papers.find(p => p.id === activeId) ?? null;
  const compareB = papers.find(p => p.id !== activeId && p.pinned) ?? papers[1] ?? null;

  const toggleBookmark = async (paperId: string) => {
    const current = papers.find(p => p.id === paperId);
    if (!current) return;
    try {
      if (current.pinned) await api.unpinPaper(paperId);
      else await api.pinPaper(paperId);
      setPapers(prev => prev.map(p => p.id === paperId ? { ...p, pinned: !p.pinned } : p));
    } catch (e) { console.error("toggleBookmark:", e); }
  };

  const openPaper = (id: string) => { setActiveId(id); setPage("explorer"); };

  const dbCount = health ? health.paper_count.toLocaleString() : (papers.length > 0 ? papers.length.toLocaleString() : "—");
  const llmLabel = health ? `${health.llm_provider} · ${health.llm_model}` : "offline";

  return (
    <div className="shell">
      <div className="topbar">
        <div className="brand">
          <span><b>Meridian</b></span>
          <button
            className="chip api-cfg-chip"
            title="Settings — API keys, LLM provider, connection"
            onClick={() => setApiConfigOpen(true)}
            style={{ marginLeft: 8, fontSize: 11, padding: "2px 8px", opacity: 0.7 }}
          >
            ⚙ Settings
          </button>
        </div>
        <div className="cmdrow">
          <div className="nav-tabs">
            <button className={"nav-tab " + (page === "explorer" ? "on" : "")} onClick={() => setPage("explorer")}>Explorer</button>
            <button className={"nav-tab " + (page === "library" ? "on" : "")} onClick={() => setPage("library")}>Library</button>
            <button className={"nav-tab " + (page === "prompts" ? "on" : "")} onClick={() => setPage("prompts")}>Prompts</button>
          </div>
          <div className="cmdk" style={{ cursor: "default" }}>
            {paper ? (
              <>
                <span style={{ color: "var(--ink-4)", fontSize: 11, flexShrink: 0 }}>{paper.source}</span>
                <span style={{ color: "var(--rule-2)", flexShrink: 0 }}>/</span>
                <span style={{
                  flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  fontSize: 13, color: "var(--ink-1)", fontWeight: 500,
                }} title={paper.title}>{paper.title}</span>
                <span style={{ fontSize: 11, color: "var(--ink-4)", flexShrink: 0 }}>{paper.date?.slice(0, 7)}</span>
              </>
            ) : (
              <span style={{ color: "var(--ink-4)", fontSize: 13 }}>No paper selected</span>
            )}
          </div>
          <button className="chip" onClick={() => setUsageOpen(true)} title="Usage — tokens & paper views" style={{ fontSize: 11 }}>
            ◈ Usage
          </button>
          <button className="chip primary" onClick={() => setIngestOpen(true)}>
            {pulling ? <><span className="sweep-dot" /> Ingesting…</> : "◆ Ingest"}
          </button>
        </div>
        <div className="account">
          <button className={"chip routing-chip " + (routing === "local" ? "ok" : "ember")} onClick={() => setRoutingOpen(!routingOpen)} title="Model routing">
            {routing === "local" ? "◈ Local" : routing === "claude" ? "◈ Claude" : routing === "gpt" ? "◈ GPT-4" : "◈ Gemini"}
            <span className="chev">▾</span>
          </button>
          <button className={"chip watch-chip " + (watchOpen ? "on" : "")} onClick={() => setWatchOpen(!watchOpen)} title="Batch processing">
            <span className="watch-dot" /> Batch
          </button>
        </div>
      </div>

      {page === "explorer" && (
        <div className="body" style={{ gridTemplateColumns: rightCollapsed
          ? "clamp(220px,22vw,300px) minmax(0,1fr) 0px"
          : "clamp(220px,22vw,300px) minmax(0,1fr) clamp(260px,24vw,320px)" }}>
          <LeftRail
            activeId={activeId}
            onSelect={setActiveId}
            onPull={onPull}
            pulling={pulling}
            onOpenIngest={() => setIngestOpen(true)}
            selectedRun={selectedRun}
            onSelectRun={setSelectedRun}
          />
          <CenterStage
            mode={mode} setMode={setMode}
            paper={paper}
            papers={papers}
            edges={citations}
            onSelect={setActiveId}
            compareB={compareB}
            rightCollapsed={rightCollapsed}
            toggleRight={() => setRightCollapsed(!rightCollapsed)}
            traversal={traversalForGraph}
            onToggleBookmark={toggleBookmark}
          />
          {!rightCollapsed && (
            <ContextRail
              paper={paper}
              trace={trace}
              mentions={mentions}
              onSelect={setActiveId}
              selectedTraversal={selectedTraversal}
              onSelectTraversal={setSelectedTraversal}
            />
          )}
        </div>
      )}

      {page === "library" && (
        <div className="library-wrap">
          <LibraryPage papers={papers} onOpenPaper={openPaper} onBack={() => setPage("explorer")} />
        </div>
      )}

      {page === "prompts" && (
        <div className="library-wrap">
          <PromptsPage />
        </div>
      )}

      {batchStatus?.running && (() => {
        const pct = batchStatus.total > 0 ? Math.min(100, Math.round((batchStatus.done / batchStatus.total) * 100)) : 0;
        const actionLabel = ({
          ocr: "OCR", summarize: "Summarising", ocr_summarize: "OCR+Sum",
          extract: "Extracting", extract_summarize: "Extract+Sum",
          embed: "Embedding", download_pdf: "Downloading PDFs",
        } as Record<string, string>)[batchStatus.action] || "Batch";
        return (
          <div className="batch-progress-bar">
            <span className="proc-spin" />
            <span className="bp-label">{actionLabel}</span>
            <div className="bp-track">
              <div className="bp-fill" style={{ width: pct + "%" }} />
            </div>
            <span className="bp-pct">{pct}%</span>
            <span className="bp-count">{batchStatus.done ?? 0}/{batchStatus.total ?? "?"}</span>
            {batchStatus.current ? <span className="bp-current">{batchStatus.current.replace(/^(arxiv|pubmed|doi):/, "")}</span> : null}
            {batchStatus.errors > 0 && <span className="bp-errors">{batchStatus.errors} err</span>}
          </div>
        );
      })()}

      <div className="statusbar">
        <button className="item sb-theme" onClick={() => setTheme(theme === "light" ? "dark" : "light")} title={theme === "light" ? "Switch to dark" : "Switch to light"}>
          <span className="sb-theme-glyph">{theme === "light" ? "◑" : "◐"}</span>
          <span className="sb-theme-lbl">{theme === "light" ? "Light" : "Dark"}</span>
        </button>
        <div className="item"><span className="led" /> DB · <b>{dbCount}</b></div>
        <div className="item">LLM · <b>{llmLabel}</b></div>
        <div className="spacer" />
        {procItems.map(item => {
          const actionLabel = ({
            ocr: "OCR", summarize: "Summarising", ocr_summarize: "OCR+Sum",
            extract: "Extracting", extract_summarize: "Extract+Sum",
            embed: "Embedding", download_pdf: "Downloading", batch: "Processing",
          } as Record<string, string>)[item.action] || item.action;
          const progress = item.pages_total > 0
            ? ` p.${item.pages_done}/${item.pages_total}`
            : item.total > 0 ? ` ${item.done}/${item.total}` : "";
          const shortId = item.paper_id.replace(/^arxiv:/, "");
          return (
            <button
              key={item.paper_id}
              className="item sb-btn proc-indicator"
              title={`${actionLabel} in progress — click to open paper`}
              onClick={() => { setActiveId(item.paper_id); setPage("explorer"); }}
              style={{ gap: 5 }}
            >
              <span className="proc-spin" />
              <span>{actionLabel}{progress}</span>
              <span style={{ opacity: 0.6, fontFamily: "var(--font-mono)", fontSize: 10 }}>
                {shortId.length > 14 ? shortId.slice(0, 14) + "…" : shortId}
              </span>
            </button>
          );
        })}
        <button className={"item sb-btn " + (chatOpen ? "on" : "")} onClick={() => setChatOpen(!chatOpen)} title="Toggle Agent (⌘`)">
          <span className="sb-dot" /> Agent <kbd>⌘`</kbd>
        </button>
        <div className="item"><span className="led ok-dim" /> {papers.filter(p => ((p.cache_flags ?? 0) & 4) || p.status === "summarized").length} summ.</div>
        <div className="item"><span className="led ok-dim" /> {papers.filter(p => (p.cache_flags ?? 0) & 32).length} extracted</div>
        <div className="item"><span className="led ok-dim" /> {papers.filter(p => (p.cache_flags ?? 0) & 2).length} embedded</div>
        <div className="item"><span className="led warn" /> {papers.filter(p => p.status === "paywalled").length} paywalled</div>
      </div>

      <RoutingMenu open={routingOpen} onClose={() => setRoutingOpen(false)} routing={routing} setRouting={setRouting} />
      <AgentWatch open={watchOpen} onClose={() => setWatchOpen(false)} />
      <UsageModal
        open={usageOpen}
        onClose={() => setUsageOpen(false)}
        onOpenPaper={openPaper}
      />
      <ApiConfigModal
        open={apiConfigOpen}
        onClose={() => setApiConfigOpen(false)}
        onReload={() => { setApiReady(false); void api.boot(); }}
      />
      <IngestModal
        open={ingestOpen}
        onClose={() => setIngestOpen(false)}
        sources={sources as unknown as Record<string, boolean>}
        toggleSource={toggleSource}
        onPull={onPull}
        pulling={pulling}
        onPaperAdded={(p) => {
          setPapers(prev => prev.some(x => x.id === p.id) ? prev : [p, ...prev]);
          window.dispatchEvent(new CustomEvent("rs:manual-paper-added", { detail: { paperId: p.id } }));
        }}
      />
      {ingestResult && (
        <IngestResultPanel
          result={ingestResult}
          onClose={() => setIngestResult(null)}
          onOpenPaper={setActiveId}
        />
      )}
      <BottomChat
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        height={chatHeight}
        setHeight={setChatHeight}
        paper={paper}
        papers={papers}
        onOpenPaper={setActiveId}
      />

      {toast && (
        <div className={"meridian-toast " + toast.type} onClick={() => setToast(null)}>
          <span className="mt-icon">{toast.type === "warn" ? "⚠" : "ℹ"}</span>
          <span className="mt-msg">{toast.msg}</span>
          <button className="mt-close" onClick={e => { e.stopPropagation(); setToast(null); }}>✕</button>
        </div>
      )}

      {editModeActive && (
        <div className="tweaks open">
          <h3>Tweaks <span className="x" onClick={() => setEditModeActive(false)}>✕</span></h3>
          <div className="tw-group">
            <div className="tw-label">Accent hue</div>
            <div className="tw-chips">
              {(["rust", "ember", "sulfur", "clay"] as const).map(a => (
                <button key={a} className={"tw-chip " + (tweaks.accent === a ? "on" : "")} onClick={() => updateTweak("accent", a)}>{a}</button>
              ))}
            </div>
          </div>
          <div className="tw-group">
            <div className="tw-label">Agentic chat</div>
            <div className="tw-chips">
              <button className={"tw-chip " + (tweaks.showChat ? "on" : "")} onClick={() => updateTweak("showChat", !tweaks.showChat)}>
                {tweaks.showChat ? "visible" : "hidden"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
