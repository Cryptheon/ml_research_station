import { useState, useEffect, useMemo } from "react";
import { api, API_BASE } from "../api";
import type { Paper } from "../types";

const LIB_BATCH_ACTIONS = [
  { id: "ocr",               label: "OCR only",                sub: "Vision LLM extracts text from PDF page images" },
  { id: "summarize",         label: "Summarize only",          sub: "Run LLM summary on existing text or abstract" },
  { id: "ocr_summarize",     label: "OCR + Summarize",         sub: "Full pipeline — vision OCR then summarize" },
  { id: "extract",           label: "PDF extract only",        sub: "Fast — reads embedded text directly, no AI needed" },
  { id: "extract_summarize", label: "PDF Extract + Summarize", sub: "Extract text then summarize — no OCR model needed" },
  { id: "embed",             label: "Embed only",              sub: "Generate semantic embeddings via configured embedding provider" },
  { id: "download_pdf",      label: "Download PDFs",           sub: "Fetch PDFs for papers that don't have them yet" },
];

interface Collection {
  id: string | number;
  name: string;
  count?: number;
  swatch?: string;
  updated?: string;
  virtual?: boolean;
  run?: IngestRun;
}

interface IngestRun {
  id: number;
  ran_at: string;
  interests?: string[];
  found: number;
  duration_seconds?: number;
}

function BatchModal({ col, paperIds, onClose }: { col: Collection; paperIds: string[]; onClose: () => void }) {
  const [action, setAction] = useState("ocr_summarize");
  const [status, setStatus] = useState<"running" | "done" | "error" | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const start = async () => {
    setStatus("running"); setMsg(null);
    try {
      const res = await api.startBatch(action, "all", paperIds) as { queued?: number; message?: string };
      if (res && res.queued && res.queued > 0) {
        setStatus("done");
        setMsg(`Queued ${res.queued} paper${res.queued !== 1 ? "s" : ""}. Track progress in the Batch panel.`);
      } else {
        setStatus("error");
        setMsg(res?.message || "No papers matched the filter.");
      }
    } catch (e) {
      setStatus("error");
      setMsg(`Error: ${String(e)}`);
    }
  };

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="ingest-modal" style={{ maxWidth: 440, minHeight: "unset" }} onClick={e => e.stopPropagation()}>
        <div className="im-head">
          <div>
            <div className="im-kicker">BATCH PROCESS</div>
            <div className="im-title" style={{ fontSize: 16 }}>{col.name}</div>
          </div>
          <button className="im-close" onClick={onClose}>✕</button>
        </div>
        <div className="im-section" style={{ paddingTop: 14, paddingBottom: 4 }}>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-4)", marginBottom: 14 }}>
            {paperIds.length} paper{paperIds.length !== 1 ? "s" : ""} in this collection
          </div>
          {LIB_BATCH_ACTIONS.map(a => (
            <div key={a.id} className="watch-item" style={{ cursor: "pointer", paddingLeft: 0, paddingRight: 0 }}
                 onClick={() => status !== "running" && setAction(a.id)}>
              <span className={"watch-toggle " + (action === a.id ? "on" : "")}><span /></span>
              <div className="watch-main">
                <div className="watch-name">{a.label}</div>
                <div className="watch-sub">{a.sub}</div>
              </div>
            </div>
          ))}
          {msg && (
            <div style={{
              marginTop: 14, padding: "10px 14px",
              background: status === "error" ? "rgba(162,62,34,0.06)" : "var(--bg-2)",
              borderLeft: `2px solid ${status === "error" ? "var(--rust)" : "var(--ok)"}`,
              fontSize: 12, color: "var(--ink-2)", lineHeight: 1.5,
            }}>{msg}</div>
          )}
        </div>
        <div className="im-foot">
          <div style={{ fontSize: 12, color: "var(--ink-4)" }}>
            Papers run one at a time. Progress in <b>Watch</b> panel.
          </div>
          <div className="im-foot-actions">
            <button className="ghost" onClick={onClose}>Close</button>
            <button className="primary" onClick={() => void start()}
                    disabled={status === "running" || status === "done"}>
              {status === "running" ? "Starting…" : status === "done" ? "Queued ✓" : "▶ Start"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const PAGE_SIZE = 24;
const SWATCHES = ["var(--rust)", "var(--ember)", "var(--sulfur)", "var(--clay)"];

function _relTime(iso?: string) {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 120) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return m > 0 ? `${h}h ${m}m ago` : `${h}h ago`;
  }
  return `${Math.floor(s / 86400)}d ago`;
}

function _papersForRun(papers: Paper[], run: IngestRun): Paper[] {
  const start = new Date(run.ran_at).getTime() - 5000;
  const end = start + 5000 + (run.duration_seconds || 120) * 1000 + 30000;
  const byWindow = papers.filter(p => {
    if (!p.created_at) return false;
    const t = new Date(p.created_at).getTime();
    return t >= start && t <= end;
  });
  if (byWindow.length === 0 && run.interests && run.interests.length) {
    const terms = run.interests.flatMap(i => {
      const full = i.toLowerCase();
      const words = full.trim().split(/\s+/);
      const truncated = words.slice(0, 2).join(" ");
      return full === truncated ? [full] : [full, truncated];
    });
    return papers.filter(p =>
      terms.some(t => p.title.toLowerCase().includes(t) || (p.abstract || "").toLowerCase().includes(t))
    );
  }
  return byWindow;
}

export function Empty({ icon, title, body, cta, onCta }: { icon: string; title: string; body: string; cta?: string; onCta?: () => void }) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <div className="empty-title">{title}</div>
      <div className="empty-body">{body}</div>
      {cta && <button className="empty-cta" onClick={onCta}>{cta}</button>}
    </div>
  );
}

function PaperRow({ p, onOpen }: { p: Paper; onOpen: (id: string) => void }) {
  const statusColor = p.status === "summarized" ? "var(--ok)" : p.status === "paywalled" ? "var(--rust)" : "var(--ink-4)";
  return (
    <article className="lib-paper-row" onClick={() => onOpen(p.id)}>
      <div className="lpr-left">
        <div className="lpr-meta">
          <span className="lpr-venue">{p.venue || p.source}</span>
          <span className="lpr-sep">·</span>
          <span className="lpr-date">{p.date?.slice(0, 7)}</span>
          <span className="lpr-sep">·</span>
          <span style={{ color: statusColor, fontSize: 10 }}>{p.status}</span>
        </div>
        <div className="lpr-title">{p.title}</div>
        <div className="lpr-authors">{(p.authors || []).slice(0, 3).join(", ")}{(p.authors?.length ?? 0) > 3 ? ` +${(p.authors?.length ?? 0) - 3}` : ""}</div>
      </div>
      <div className="lpr-right">
        {(p.topics || []).slice(0, 2).map(t => <span key={t} className="tag" style={{ fontSize: 10 }}>{t}</span>)}
        <span className="lpr-go">↗</span>
      </div>
    </article>
  );
}

function Pager({ page, total, pageSize, onChange }: { page: number; total: number; pageSize: number; onChange: (p: number) => void }) {
  const pages = Math.ceil(total / pageSize);
  if (pages <= 1) return null;
  return (
    <div className="lib-pager">
      <button className="ghost" disabled={page === 0} onClick={() => onChange(page - 1)}>← Prev</button>
      <span className="lib-pager-info">Page {page + 1} of {pages} · {total} papers</span>
      <button className="ghost" disabled={page >= pages - 1} onClick={() => onChange(page + 1)}>Next →</button>
    </div>
  );
}

const LIB_SOURCE_FILTERS = [
  { key: "all",        label: "All" },
  { key: "arxiv",      label: "ARXIV",    color: "var(--ink-4)" },
  { key: "biorxiv",    label: "BIORXIV",  color: "#a8c97a" },
  { key: "pubmed",     label: "PUBMED",   color: "#e07060" },
  { key: "openreview", label: "OPENREV",  color: "#b39ddb" },
  { key: "wiki",       label: "WIKI",     color: "#4db6ac" },
  { key: "web",        label: "WEB",      color: "#7b9fd4" },
];

function _matchesSourceFilter(p: Paper, src: string): boolean {
  if (src === "all") return true;
  const source = (p.source || "").toLowerCase();
  if (src === "web")        return p.id.startsWith("web:");
  if (src === "wiki")       return p.id.startsWith("wikipedia:");
  if (src === "biorxiv")    return source === "biorxiv" || (p.venue || "").toLowerCase().includes("biorxiv");
  if (src === "pubmed")     return source === "pubmed"  || p.id.startsWith("pubmed:");
  if (src === "openreview") return source === "openreview";
  if (src === "arxiv")      return !p.id.startsWith("web:") && !p.id.startsWith("wikipedia:") && !p.id.startsWith("pubmed:") && source !== "biorxiv" && source !== "openreview";
  return true;
}

function AllTab({ papers, onOpen, q }: { papers: Paper[]; onOpen: (id: string) => void; q: string }) {
  const [page, setPage] = useState(0);
  const [srcFilter, setSrcFilter] = useState("all");

  const filtered = useMemo(() => {
    let list = papers;
    if (srcFilter !== "all") list = list.filter(p => _matchesSourceFilter(p, srcFilter));
    if (q) {
      const lq = q.toLowerCase();
      list = list.filter(p =>
        p.title.toLowerCase().includes(lq) ||
        (p.authors || []).some(a => a.toLowerCase().includes(lq)) ||
        (p.abstract || "").toLowerCase().includes(lq)
      );
    }
    return list;
  }, [papers, q, srcFilter]);

  useEffect(() => setPage(0), [q, srcFilter]);

  const slice = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  return (
    <>
      <div className="lib-source-filter">
        {LIB_SOURCE_FILTERS.map(opt => (
          <button
            key={opt.key}
            className={"lib-sfbtn " + (srcFilter === opt.key ? "on" : "")}
            onClick={() => setSrcFilter(opt.key)}
            style={srcFilter === opt.key && opt.color ? { color: opt.color, borderColor: opt.color } : {}}
          >{opt.label}</button>
        ))}
      </div>
      {filtered.length === 0 ? (
        <Empty icon="⌕" title="No papers match" body={q ? `No results for "${q}"` : "No papers in this category."} />
      ) : (
        <>
          <div className="lib-paper-list">
            {slice.map(p => <PaperRow key={p.id} p={p} onOpen={onOpen} />)}
          </div>
          <Pager page={page} total={filtered.length} pageSize={PAGE_SIZE} onChange={setPage} />
        </>
      )}
    </>
  );
}

function CollectionDetail({ col, allPapers, onOpen, onBack }: { col: Collection; allPapers: Paper[]; onOpen: (id: string) => void; onBack: () => void }) {
  const [page, setPage] = useState(0);
  const [batchOpen, setBatchOpen] = useState(false);
  const items = col.virtual && col.run ? _papersForRun(allPapers, col.run) : allPapers;
  const paperIds = items.map(p => p.id);
  const slice = items.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  return (
    <div>
      <div className="col-detail-head">
        <button className="ghost" onClick={onBack}>← Collections</button>
        <div className="col-detail-name">{col.name}</div>
        <div className="col-detail-meta">{items.length} papers{col.run ? ` · pulled ${_relTime(col.run.ran_at)}` : ""}</div>
        {items.length > 0 && (
          <button className="ghost small" style={{ marginLeft: "auto" }} onClick={() => setBatchOpen(true)}>▶ Batch</button>
        )}
      </div>
      {items.length === 0 ? (
        <Empty icon="◫" title="No papers found" body="Papers from this pull may predate ingestion tracking. Try re-pulling with these keywords." />
      ) : (
        <>
          <div className="lib-paper-list">
            {slice.map(p => <PaperRow key={p.id} p={p} onOpen={onOpen} />)}
          </div>
          <Pager page={page} total={items.length} pageSize={PAGE_SIZE} onChange={setPage} />
        </>
      )}
      {batchOpen && <BatchModal col={col} paperIds={paperIds} onClose={() => setBatchOpen(false)} />}
    </div>
  );
}

interface DeleteConfirm { col: Collection; deletePapers: boolean; }

function CollectionsTab({ ingestRuns, realCollections, allPapers, onOpen, onCreated, q }: {
  ingestRuns: IngestRun[];
  realCollections: Collection[];
  allPapers: Paper[];
  onOpen: (id: string) => void;
  onCreated: () => void;
  q: string;
}) {
  const [detail, setDetail] = useState<Collection | null>(null);
  const [batchCol, setBatchCol] = useState<Collection | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<DeleteConfirm | null>(null);
  const [deleting, setDeleting] = useState(false);

  const confirmDelete = async () => {
    if (!deleteConfirm) return;
    setDeleting(true);
    try {
      const { col, deletePapers } = deleteConfirm;
      if (col.virtual && col.run) {
        await api.deleteIngest(String(col.run.id), deletePapers);
      } else {
        await api.deleteCollection(String(col.id), deletePapers);
      }
      onCreated();
    } catch { /* ignore */ }
    setDeleting(false);
    setDeleteConfirm(null);
  };

  if (detail) {
    return (
      <CollectionDetail col={detail} allPapers={allPapers} onOpen={onOpen} onBack={() => setDetail(null)} />
    );
  }

  const createCollection = async () => {
    if (!newName.trim()) return;
    setSaving(true);
    try {
      const r = await fetch(`${API_BASE}/users/me/collections`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName.trim() }),
      });
      if (r.ok) { onCreated(); setNewName(""); setCreating(false); }
    } catch { /* ignore */ }
    setSaving(false);
  };

  const virtualCols: Collection[] = ingestRuns.map((run, i) => ({
    id: `run-${run.id}`,
    virtual: true,
    run,
    name: (run.interests || []).slice(0, 3).join(" · ") || "Unnamed pull",
    count: run.found,
    swatch: SWATCHES[i % SWATCHES.length],
    updated: run.ran_at,
  }));

  const _allCols = [
    ...virtualCols,
    ...realCollections.map(c => ({ ...c, virtual: false })),
  ];
  const allCols = q
    ? _allCols.filter(c => c.name.toLowerCase().includes(q.toLowerCase()))
    : _allCols;

  return (
    <>
      {batchCol && (
        <BatchModal
          col={batchCol}
          paperIds={batchCol.virtual && batchCol.run ? _papersForRun(allPapers, batchCol.run).map(p => p.id) : allPapers.map(p => p.id)}
          onClose={() => setBatchCol(null)}
        />
      )}
      {deleteConfirm && (
        <div style={{ position: "fixed", inset: 0, zIndex: 200, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center" }}
             onClick={() => !deleting && setDeleteConfirm(null)}>
          <div style={{ background: "var(--bg-1)", border: "1px solid var(--rule)", borderRadius: 8, padding: "24px 28px", maxWidth: 380, width: "90%", boxShadow: "0 8px 32px rgba(0,0,0,0.4)" }}
               onClick={e => e.stopPropagation()}>
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 8 }}>Remove "{deleteConfirm.col.name}"?</div>
            <div style={{ fontSize: 13, color: "var(--ink-3)", marginBottom: 16, lineHeight: 1.5 }}>
              {deleteConfirm.deletePapers
                ? `This will permanently delete the collection and all ${deleteConfirm.col.count ?? 0} papers inside it from the database.`
                : "This will remove the collection. The papers will remain in your library."}
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 14 }}>
              <input type="checkbox" id="del-papers-chk" checked={deleteConfirm.deletePapers}
                     onChange={e => setDeleteConfirm(c => c ? { ...c, deletePapers: e.target.checked } : null)} />
              <label htmlFor="del-papers-chk" style={{ fontSize: 13, cursor: "pointer" }}>Also delete papers from database</label>
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="ghost small" disabled={deleting} onClick={() => setDeleteConfirm(null)}>Cancel</button>
              <button disabled={deleting} onClick={() => void confirmDelete()}
                      style={{ padding: "5px 14px", fontSize: 12, fontWeight: 600, borderRadius: 4, border: "none", background: "var(--rust)", color: "#fff", cursor: deleting ? "wait" : "pointer", opacity: deleting ? 0.7 : 1 }}>
                {deleting ? "Removing…" : "Remove"}
              </button>
            </div>
          </div>
        </div>
      )}
      <div className="col-grid">
        {allCols.map(col => (
          <article key={String(col.id)} className="col-card" onClick={() => setDetail(col)} style={{ cursor: "pointer" }}>
            <div className="col-head">
              <div className="col-swatch" style={{ background: col.swatch || "var(--rust)" }} />
              <div className="col-name">{col.name}</div>
              {col.virtual && <span style={{ fontSize: 9, color: "var(--ink-4)", marginLeft: "auto" }}>pull</span>}
            </div>
            <div className="col-count" style={{ padding: "4px 14px", fontSize: 12, color: "var(--ink-3)" }}>
              <b>{col.count ?? 0}</b> papers
            </div>
            <div className="col-foot">
              <span>{_relTime(col.updated)}</span>
              <button className="ghost small" onClick={e => { e.stopPropagation(); setBatchCol(col); }} title="Batch OCR / summarize this collection">▶ Batch</button>
              <button className="ghost small" onClick={e => { e.stopPropagation(); setDeleteConfirm({ col, deletePapers: false }); }} title={col.virtual ? "Remove this pull" : "Remove collection"} style={{ color: "var(--rust)" }}>Remove</button>
              <button className="ghost small" onClick={e => { e.stopPropagation(); setDetail(col); }}>Open →</button>
            </div>
          </article>
        ))}

        <article className="col-card new" onClick={() => !creating && setCreating(true)}>
          {creating ? (
            <div style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 8 }} onClick={e => e.stopPropagation()}>
              <input
                autoFocus
                placeholder="Collection name…"
                value={newName}
                onChange={e => setNewName(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") void createCollection(); if (e.key === "Escape") setCreating(false); }}
                style={{ font: "inherit", fontSize: 13, border: "1px solid var(--rule)", borderRadius: 3, padding: "4px 8px", background: "var(--bg)" }}
              />
              <div style={{ display: "flex", gap: 6 }}>
                <button className="ghost small" onClick={() => setCreating(false)}>Cancel</button>
                <button className="ghost small" style={{ color: "var(--rust)" }} disabled={saving || !newName.trim()} onClick={() => void createCollection()}>
                  {saving ? "Saving…" : "Create"}
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="col-new-glyph">+</div>
              <div className="col-new-label">New collection</div>
              <div className="col-new-hint">Group papers by topic or project</div>
            </>
          )}
        </article>
      </div>
    </>
  );
}

interface Chat { id: number; paper_id?: string; title?: string; last_message?: string; updated_at: string; message_count: number; }

function ChatsTab({ chats, papers, onOpenPaper, onDelete, q }: { chats: Chat[]; papers: Paper[]; onOpenPaper: (id: string) => void; onDelete: (id: number) => void; q: string }) {
  const paperMap = Object.fromEntries(papers.map(p => [p.id, p]));
  const visibleChats = q
    ? chats.filter(c => {
        const lq = q.toLowerCase();
        return (c.title || "").toLowerCase().includes(lq) || (c.last_message || "").toLowerCase().includes(lq);
      })
    : chats;
  return (
    <div className="col-grid" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {visibleChats.map(chat => {
        const paper = chat.paper_id ? paperMap[chat.paper_id] : null;
        const ago = (() => {
          const d = new Date(chat.updated_at);
          const mins = Math.floor((Date.now() - d.getTime()) / 60000);
          if (mins < 2) return "just now";
          if (mins < 60) return `${mins}m ago`;
          const hrs = Math.floor(mins / 60);
          if (hrs < 24) return `${hrs}h ago`;
          return `${Math.floor(hrs / 24)}d ago`;
        })();
        return (
          <div key={chat.id} className="col-card" style={{ cursor: "default", position: "relative" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 13, color: "var(--ink-1)", marginBottom: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {chat.title || "Conversation"}
                </div>
                {paper && (
                  <div style={{ fontSize: 11, color: "var(--rust)", cursor: "pointer", marginBottom: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                       onClick={() => onOpenPaper(paper.id)}>
                    ⌖ {paper.title}
                  </div>
                )}
                {chat.last_message && (
                  <div style={{ fontSize: 12, color: "var(--ink-3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {chat.last_message}
                  </div>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6, flexShrink: 0 }}>
                <span style={{ fontSize: 11, color: "var(--ink-4)" }}>{ago}</span>
                <span style={{ fontSize: 11, color: "var(--ink-4)" }}>{chat.message_count} msg{chat.message_count !== 1 ? "s" : ""}</span>
                <button className="ghost" style={{ fontSize: 11, padding: "2px 8px" }} onClick={() => onDelete(chat.id)}>delete</button>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export interface LibraryPageProps {
  papers: Paper[];
  onOpenPaper: (id: string) => void;
  onBack: () => void;
}

export function LibraryPage({ papers, onOpenPaper, onBack }: LibraryPageProps) {
  const [tab, setTab] = useState("all");
  const [q, setQ] = useState("");
  const [ingestRuns, setIngestRuns] = useState<IngestRun[]>([]);
  const [realCollections, setRealCollections] = useState<Collection[]>([]);
  const [chats, setChats] = useState<Chat[]>([]);
  const [manualIds, setManualIds] = useState<string[]>([]);

  const loadData = () => {
    Promise.all([
      fetch(`${API_BASE}/users/me/ingests?limit=20`).then(r => r.ok ? r.json() : []),
      fetch(`${API_BASE}/users/me/collections`).then(r => r.ok ? r.json() : []),
      fetch(`${API_BASE}/users/me/chats`).then(r => r.ok ? r.json() : []),
      api.fetchManuallyAdded(),
    ]).then(([runs, cols, chs, manual]) => {
      setIngestRuns((runs as IngestRun[]) || []);
      setRealCollections((cols as Collection[]) || []);
      setChats((chs as Chat[]) || []);
      setManualIds(((manual as Array<{ paper_id: string }>) || []).map(m => m.paper_id));
    }).catch(() => {});
  };

  useEffect(() => { loadData(); }, []);

  useEffect(() => {
    const handler = (ev: Event) => {
      const id = (ev as CustomEvent<{ paperId?: string }>).detail?.paperId;
      if (id) setManualIds(prev => prev.includes(id) ? prev : [id, ...prev]);
    };
    window.addEventListener("rs:manual-paper-added", handler);
    return () => window.removeEventListener("rs:manual-paper-added", handler);
  }, []);

  const pinned = papers.filter(p => p.pinned);
  const manualPapers = useMemo(
    () => papers.filter(p => manualIds.includes(p.id)),
    [papers, manualIds]
  );

  const tabs: [string, string, number][] = [
    ["all",         "All papers",  papers.length],
    ["pins",        "Bookmarks",   pinned.length],
    ["manual",      "Manual adds", manualPapers.length],
    ["collections", "Collections", ingestRuns.length + realCollections.length],
    ["notebooks",   "Notebooks",   0],
    ["chats",       "Chats",       chats.length],
  ];

  return (
    <div className="library">
      <div className="lib-hero">
        <div className="lib-hero-kick">YOUR CORPUS</div>
        <h1 className="lib-hero-title">Library</h1>
        <div className="lib-hero-meta">
          <span><b>{papers.length}</b> papers</span>
          <span className="sep">·</span>
          <span><b>{pinned.length}</b> bookmarked</span>
          <span className="sep">·</span>
          <span><b>{manualPapers.length}</b> manual</span>
          <span className="sep">·</span>
          <span><b>{ingestRuns.length}</b> pulls</span>
          <span className="sep">·</span>
          <span><b>{realCollections.length}</b> collections</span>
        </div>
        <div className="lib-hero-search">
          <span className="s-icon">⌕</span>
          <input placeholder="Search papers by title, author, or abstract…" value={q} onChange={e => setQ(e.target.value)} />
          <kbd>⌘F</kbd>
        </div>
      </div>

      <div className="lib-tabrow">
        {tabs.map(([k, lbl, n]) => (
          <button key={k} className={"lib-tab " + (tab === k ? "on" : "")} onClick={() => setTab(k)}>
            <span>{lbl}</span>
            {n > 0 && <span className="n">{n}</span>}
          </button>
        ))}
        <div className="lib-tabrow-spacer" />
        <button className="lib-mini-action" onClick={onBack}>← Explorer</button>
      </div>

      <div className="lib-body">
        {tab === "all" && <AllTab papers={papers} onOpen={id => { onOpenPaper(id); }} q={q} />}

        {tab === "pins" && (pinned.length === 0 ? (
          <Empty icon="★" title="No bookmarks yet" body="Bookmark a paper from the reader to keep it one click away." cta="Back to Explorer" onCta={onBack} />
        ) : (
          <AllTab papers={pinned} onOpen={onOpenPaper} q={q} />
        ))}

        {tab === "manual" && (manualPapers.length === 0 ? (
          <Empty icon="⊕" title="No manually added papers" body='Use "Add by ID" in the Ingest modal to add specific arXiv papers by ID or URL.' />
        ) : (
          <AllTab papers={manualPapers} onOpen={onOpenPaper} q={q} />
        ))}

        {tab === "collections" && (
          <CollectionsTab
            ingestRuns={ingestRuns}
            realCollections={realCollections}
            allPapers={papers}
            onOpen={onOpenPaper}
            onCreated={loadData}
            q={q}
          />
        )}

        {tab === "notebooks" && (
          <Empty icon="◈" title="No notebooks yet" body="Notebooks let you draft ideas with paper excerpts inline. Coming in Phase 4." />
        )}

        {tab === "chats" && (
          chats.length === 0
            ? <Empty icon="›" title="No saved chats" body="Start a conversation in the ⌘` chat drawer — conversations are saved per paper." />
            : <ChatsTab chats={chats} papers={papers} onOpenPaper={onOpenPaper} q={q} onDelete={id => {
                api.deleteChat(String(id)).then(() => loadData()).catch(() => {});
              }} />
        )}
      </div>
    </div>
  );
}
