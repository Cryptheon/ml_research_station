import { useState, useEffect, useMemo } from "react";
import { api } from "../api";
import type { Paper } from "../types";

const INTEREST_SUGGESTIONS = [
  "computer vision", "emergence of intelligence", "protein design with LLMs",
  "mechanistic interpretability", "diffusion samplers", "world models",
  "RLHF alternatives", "long-context inference", "neural operators",
  "single-cell foundation models", "causal representation",
];

interface ArxivCategory { id: string; label: string; group: string; }

const ARXIV_CATEGORIES: ArxivCategory[] = [
  { id: "cs.AI",  label: "Artificial Intelligence",           group: "cs" },
  { id: "cs.CL",  label: "Computation and Language",          group: "cs" },
  { id: "cs.CV",  label: "Computer Vision",                   group: "cs" },
  { id: "cs.LG",  label: "Machine Learning",                  group: "cs" },
  { id: "cs.NE",  label: "Neural and Evolutionary Computing", group: "cs" },
  { id: "cs.RO",  label: "Robotics",                          group: "cs" },
  { id: "cs.IR",  label: "Information Retrieval",             group: "cs" },
  { id: "cs.HC",  label: "Human-Computer Interaction",        group: "cs" },
  { id: "cs.MA",  label: "Multiagent Systems",                group: "cs" },
  { id: "cs.CR",  label: "Cryptography and Security",         group: "cs" },
  { id: "cs.DB",  label: "Databases",                         group: "cs" },
  { id: "cs.DC",  label: "Distributed Computing",             group: "cs" },
  { id: "cs.DS",  label: "Data Structures and Algorithms",    group: "cs" },
  { id: "cs.GR",  label: "Graphics",                          group: "cs" },
  { id: "cs.GT",  label: "Game Theory",                       group: "cs" },
  { id: "cs.IT",  label: "Information Theory",                group: "cs" },
  { id: "cs.NA",  label: "Numerical Analysis",                group: "cs" },
  { id: "cs.NI",  label: "Networking and Internet",           group: "cs" },
  { id: "cs.PL",  label: "Programming Languages",             group: "cs" },
  { id: "cs.SE",  label: "Software Engineering",              group: "cs" },
  { id: "cs.SY",  label: "Systems and Control",               group: "cs" },
  { id: "stat.ML", label: "Machine Learning",  group: "stat" },
  { id: "stat.ME", label: "Methodology",        group: "stat" },
  { id: "stat.TH", label: "Statistics Theory",  group: "stat" },
  { id: "stat.AP", label: "Applications",       group: "stat" },
  { id: "stat.CO", label: "Computation",        group: "stat" },
  { id: "math.OC", label: "Optimization and Control", group: "math" },
  { id: "math.ST", label: "Statistics Theory",         group: "math" },
  { id: "math.NA", label: "Numerical Analysis",        group: "math" },
  { id: "math.PR", label: "Probability",               group: "math" },
  { id: "math.IT", label: "Information Theory",        group: "math" },
  { id: "eess.AS", label: "Audio and Speech Processing", group: "eess" },
  { id: "eess.IV", label: "Image and Video Processing",  group: "eess" },
  { id: "eess.SP", label: "Signal Processing",           group: "eess" },
  { id: "eess.SY", label: "Systems and Control",         group: "eess" },
  { id: "q-bio.BM", label: "Biomolecules",         group: "q-bio" },
  { id: "q-bio.CB", label: "Cell Behavior",         group: "q-bio" },
  { id: "q-bio.GN", label: "Genomics",              group: "q-bio" },
  { id: "q-bio.NC", label: "Neurons and Cognition", group: "q-bio" },
  { id: "q-bio.QM", label: "Quantitative Methods",  group: "q-bio" },
  { id: "q-bio.PE", label: "Populations and Evolution", group: "q-bio" },
  { id: "quant-ph",        label: "Quantum Physics",        group: "physics" },
  { id: "physics.comp-ph", label: "Computational Physics",  group: "physics" },
  { id: "physics.data-an", label: "Data Analysis",          group: "physics" },
  { id: "econ.EM", label: "Econometrics",          group: "econ" },
  { id: "econ.GN", label: "General Economics",     group: "econ" },
  { id: "econ.TH", label: "Theoretical Economics", group: "econ" },
];

const BIORXIV_CATEGORIES: string[] = [
  "animal behavior and cognition", "biochemistry", "bioengineering", "bioinformatics",
  "biophysics", "cancer biology", "cell biology", "clinical trials",
  "developmental biology", "ecology", "epidemiology", "evolutionary biology",
  "genetics", "genomics", "immunology", "microbiology", "molecular biology",
  "neuroscience", "paleontology", "pathology", "pharmacology and toxicology",
  "physiology", "plant biology", "scientific communication and education",
  "synthetic biology", "systems biology", "zoology",
];

interface CategoryPickerProps {
  title: string;
  hint: string;
  categories: string[] | ArxivCategory[];
  selected: string[];
  onToggle: (id: string) => void;
  search: string;
  onSearch: (v: string) => void;
}

function CategoryPicker({ title, hint, categories, selected, onToggle, search, onSearch }: CategoryPickerProps) {
  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    if (!q) return categories;
    return (categories as Array<string | ArxivCategory>).filter(c =>
      (typeof c === "string" ? c : `${c.id} ${c.label}`).toLowerCase().includes(q)
    );
  }, [categories, search]);

  const isBiorxiv = typeof categories[0] === "string";

  return (
    <div className="im-section">
      <div className="im-label">
        <span>{title}</span>
        <span className="im-label-hint">
          {selected.length ? `${selected.length} selected` : hint}
        </span>
      </div>
      {selected.length > 0 && (
        <div className="im-cat-selected">
          {selected.map(id => (
            <span key={id} className="im-tag im-cat-tag">
              {id}
              <button onClick={() => onToggle(id)}>✕</button>
            </span>
          ))}
        </div>
      )}
      <input
        className="im-cat-search"
        placeholder={`Search ${title.toLowerCase()}…`}
        value={search}
        onChange={e => onSearch(e.target.value)}
      />
      <div className="im-cat-grid">
        {(filtered as Array<string | ArxivCategory>).map(c => {
          const id = isBiorxiv ? c as string : (c as ArxivCategory).id;
          const label = isBiorxiv ? c as string : (c as ArxivCategory).label;
          const group = isBiorxiv ? null : (c as ArxivCategory).group;
          const on = selected.includes(id);
          return (
            <button
              key={id}
              className={"im-cat-pill" + (on ? " on" : "")}
              onClick={() => onToggle(id)}
              title={group ? `${id} · ${label}` : label}
            >
              {group && <span className="im-cat-group">{group}</span>}
              <span className="im-cat-label">{group ? id.split(".").pop() || id : id}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function _isoToday() {
  return new Date().toISOString().slice(0, 10);
}

function _isoOffset(days: number) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

interface IngestPlanStep { step: string; note: string; }
interface IngestPlan { steps: IngestPlanStep[]; estimate_candidates: number; estimate_summarise: number; }

export interface IngestModalProps {
  open: boolean;
  onClose: () => void;
  sources: Record<string, boolean>;
  toggleSource: (s: string) => void;
  onPull: (interests: string[], sources: string[], dateParams: Record<string, unknown>, arxivCats: string[] | null, biorxivCats: string[] | null, wikiLangs: string[] | null) => void;
  pulling: boolean;
  onPaperAdded?: (paper: Paper) => void;
}

export function IngestModal({ open, onClose, sources, toggleSource, onPull, pulling, onPaperAdded }: IngestModalProps) {
  const [interests, setInterests] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [history, setHistory] = useState<string[][]>([]);
  const [livePlan, setLivePlan] = useState<IngestPlan | null>(null);
  const [dateMode, setDateMode] = useState("lookback");
  const [lookbackDays, setLookbackDays] = useState(14);
  const [dateFrom, setDateFrom] = useState(_isoOffset(14));
  const [dateTo, setDateTo] = useState(_isoToday());
  const [byIdDraft, setByIdDraft] = useState("");
  const [byIdState, setByIdState] = useState<"loading" | "ok" | "error" | null>(null);
  const [byIdMsg, setByIdMsg] = useState("");
  const [arxivCats, setArxivCats] = useState<string[]>([]);
  const [arxivSearch, setArxivSearch] = useState("");
  const [biorxivCats, setBiorxivCats] = useState<string[]>([]);
  const [biorxivSearch, setBiorxivSearch] = useState("");
  const [wikiLangs, setWikiLangs] = useState(["en"]);

  useEffect(() => {
    if (!open) return;
    try {
      const h = JSON.parse(localStorage.getItem("mpe:ingestHistory") || "[]") as string[][];
      setHistory(h);
      const last = JSON.parse(localStorage.getItem("mpe:ingestLast") || "[]") as string[];
      if (last.length) setInterests(last);
    } catch { /* ignore */ }
  }, [open]);

  useEffect(() => {
    if (!open || !interests.length) { setLivePlan(null); return; }
    const activeSrcs = Object.keys(sources).filter(s => sources[s]);
    api.fetchIngestPlan(interests, activeSrcs).then(raw => {
      const p = raw as IngestPlan | null;
      if (p) setLivePlan(p);
    }).catch(() => {});
  }, [open, interests.length, JSON.stringify(Object.keys(sources).filter(s => sources[s]))]);

  const add = (t: string) => {
    const clean = t.trim().toLowerCase();
    if (!clean || interests.includes(clean)) return;
    setInterests([...interests, clean]);
    setDraft("");
  };

  const remove = (t: string) => setInterests(interests.filter(x => x !== t));

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      add(draft);
    } else if (e.key === "Backspace" && !draft && interests.length) {
      setInterests(interests.slice(0, -1));
    }
  };

  const toggleArxivCat = (id: string) =>
    setArxivCats(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  const toggleBiorxivCat = (id: string) =>
    setBiorxivCats(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  const toggleWikiLang = (lang: string) =>
    setWikiLangs(prev => prev.includes(lang) ? (prev.length > 1 ? prev.filter(x => x !== lang) : prev) : [...prev, lang]);

  const activeSrcs = Object.keys(sources).filter(s => sources[s]);
  const estimate = Math.floor(
    (interests.length * 120 + 40) *
    (activeSrcs.length * 0.5)
  );

  const dateParams = useMemo(() => {
    if (dateMode === "range") return { date_from: dateFrom, date_to: dateTo };
    return { window_days: lookbackDays };
  }, [dateMode, dateFrom, dateTo, lookbackDays]);

  const run = () => {
    if (!interests.length) return;
    try {
      const newHist = [interests, ...history.filter(h => JSON.stringify(h) !== JSON.stringify(interests))].slice(0, 5);
      localStorage.setItem("mpe:ingestHistory", JSON.stringify(newHist));
      localStorage.setItem("mpe:ingestLast", JSON.stringify(interests));
      setHistory(newHist);
    } catch { /* ignore */ }
    const active = Object.keys(sources).filter(s => sources[s]);
    onPull(
      interests,
      active,
      dateParams,
      arxivCats.length ? arxivCats : null,
      biorxivCats.length ? biorxivCats : null,
      wikiLangs.length ? wikiLangs : null,
    );
    setTimeout(() => onClose(), 400);
  };

  const fetchById = () => {
    const raw = byIdDraft.trim();
    if (!raw) return;
    setByIdState("loading");
    setByIdMsg("");
    api.ingestById(raw)
      .then(({ paper }: { paper: Paper }) => {
        setByIdState("ok");
        setByIdMsg(`Added: ${paper.title}`);
        setByIdDraft("");
        onPaperAdded?.(paper);
      })
      .catch((err: Error) => {
        setByIdState("error");
        setByIdMsg(String(err.message || err));
      });
  };

  if (!open) return null;

  const lastStats = (() => {
    try { return JSON.parse(localStorage.getItem("mpe:ingestStats") || "null") as Record<string, number> | null; } catch { return null; }
  })();

  const plan: IngestPlanStep[] | null = interests.length === 0 ? null :
    (livePlan ? livePlan.steps : [
      { step: "embed",     note: `${interests.length} interest vector${interests.length > 1 ? "s" : ""}` },
      { step: "query",     note: `${activeSrcs.length} sources · ${dateMode === "range" ? `${dateFrom} → ${dateTo}` : `last ${lookbackDays}d`}` },
      { step: "rank",      note: "cosine + recency + venue weight" },
      { step: "dedup",     note: "title + DOI hash" },
      { step: "summarise", note: `top ~${Math.min(estimate, 40)} via ${localStorage.getItem("mpe:routing") || "local"}` },
    ]);

  const arXivActive = sources["arXiv"];
  const bioRxivActive = sources["bioRxiv"];
  const wikiActive = sources["Wikipedia"];

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="ingest-modal" onClick={e => e.stopPropagation()}>
        <div className="im-head">
          <div>
            <div className="im-kicker">Ingest</div>
            <div className="im-title">Pull papers by interest</div>
          </div>
          <button className="im-close" onClick={onClose}>✕</button>
        </div>

        <div className="im-section im-byid">
          <div className="im-label">
            <span>Add by ID</span>
            <span className="im-label-hint">arXiv ID, URL, or arxiv:XXXX.XXXXX</span>
          </div>
          <div className="im-byid-row">
            <input
              className="im-byid-input"
              placeholder="e.g. 2304.00001 · arxiv.org/abs/… · arxiv.org/pdf/…"
              value={byIdDraft}
              onChange={e => { setByIdDraft(e.target.value); setByIdState(null); setByIdMsg(""); }}
              onKeyDown={e => e.key === "Enter" && fetchById()}
              disabled={byIdState === "loading"}
            />
            <button
              className="primary"
              onClick={fetchById}
              disabled={!byIdDraft.trim() || byIdState === "loading"}
              style={{ flexShrink: 0, whiteSpace: "nowrap" }}
            >
              {byIdState === "loading" ? "…" : "Add"}
            </button>
          </div>
          {byIdMsg && (
            <div style={{ marginTop: 6, fontSize: 11, lineHeight: 1.4, color: byIdState === "error" ? "var(--rust)" : "var(--green, #3a8a6a)" }}>
              {byIdState === "ok" ? "✓ " : "✕ "}{byIdMsg}
            </div>
          )}
        </div>

        {lastStats && (
          <div className="im-section im-status">
            <div className="im-label"><span>Last pull</span><span className="im-label-hint">{lastStats.when as unknown as string}</span></div>
            <div className="im-stats">
              <div className="im-stat"><div className="im-stat-num">{lastStats.found}</div><div className="im-stat-k">new · kept</div></div>
              <div className="im-stat"><div className="im-stat-num">{lastStats.scanned}</div><div className="im-stat-k">scanned</div></div>
              <div className="im-stat"><div className="im-stat-num">{Math.max(1, Math.floor(lastStats.scanned / 60))}<span>s</span></div><div className="im-stat-k">duration</div></div>
              <div className="im-stat"><div className="im-stat-num">{((lastStats.found / Math.max(lastStats.scanned, 1)) * 100).toFixed(1)}<span>%</span></div><div className="im-stat-k">keep rate</div></div>
            </div>
          </div>
        )}

        <div className="im-section">
          <div className="im-label">
            <span>Interests</span>
            <span className="im-label-hint">the model uses these as retrieval queries</span>
          </div>
          <div className="im-tags">
            {interests.map(t => (
              <span key={t} className="im-tag">
                {t}<button onClick={() => remove(t)}>✕</button>
              </span>
            ))}
            <input
              className="im-tag-input"
              placeholder={interests.length ? "" : "e.g. protein design with LLMs"}
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              autoFocus
            />
          </div>
          <div className="im-sugg-row">
            <span className="im-mini-label">try</span>
            {INTEREST_SUGGESTIONS.filter(s => !interests.includes(s)).slice(0, 6).map(s => (
              <button key={s} className="im-sugg" onClick={() => add(s)}>+ {s}</button>
            ))}
          </div>
        </div>

        <div className="im-section">
          <div className="im-label">
            <span>Sources</span>
            <span className="im-label-hint">{activeSrcs.length} active</span>
          </div>
          <div className="im-sources">
            {Object.keys(sources).map(s => (
              <button key={s} className={"im-src " + (sources[s] ? "on" : "")} onClick={() => toggleSource(s)} title={s}>{s}</button>
            ))}
          </div>
        </div>

        {arXivActive && (
          <CategoryPicker
            title="arXiv categories" hint="all categories if none selected"
            categories={ARXIV_CATEGORIES} selected={arxivCats} onToggle={toggleArxivCat}
            search={arxivSearch} onSearch={setArxivSearch}
          />
        )}

        {bioRxivActive && (
          <CategoryPicker
            title="bioRxiv categories" hint="all categories if none selected"
            categories={BIORXIV_CATEGORIES} selected={biorxivCats} onToggle={toggleBiorxivCat}
            search={biorxivSearch} onSearch={setBiorxivSearch}
          />
        )}

        {wikiActive && (
          <div className="im-section">
            <div className="im-label">
              <span>Wikipedia languages</span>
              <span className="im-label-hint">ISO codes — searches all selected editions</span>
            </div>
            <div className="im-cat-grid" style={{ gap: 6 }}>
              {["en","de","fr","es","it","pt","nl","pl","ru","ja","zh","ar","ko","sv","fi"].map(lang => (
                <button key={lang} className={"im-cat-pill" + (wikiLangs.includes(lang) ? " on" : "")} onClick={() => toggleWikiLang(lang)} title={lang}>
                  <span className="im-cat-label">{lang}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="im-section">
          <div className="im-label">
            <span>Date window</span>
            <div className="im-date-toggle">
              <button className={"im-date-mode " + (dateMode === "lookback" ? "on" : "")} onClick={() => setDateMode("lookback")}>Lookback</button>
              <button className={"im-date-mode " + (dateMode === "range" ? "on" : "")} onClick={() => setDateMode("range")}>Date range</button>
            </div>
          </div>
          {dateMode === "lookback" ? (
            <div className="im-lookback">
              <input type="range" min={1} max={365} value={lookbackDays} onChange={e => setLookbackDays(Number(e.target.value))} style={{ flex: 1 }} />
              <span className="im-lookback-val">last <b>{lookbackDays}</b> day{lookbackDays !== 1 ? "s" : ""}</span>
            </div>
          ) : (
            <div className="im-daterange">
              <label className="im-date-label">From
                <input type="date" value={dateFrom} max={dateTo} onChange={e => setDateFrom(e.target.value)} className="im-date-input" />
              </label>
              <span className="im-date-arrow">→</span>
              <label className="im-date-label">To
                <input type="date" value={dateTo} min={dateFrom} max={_isoToday()} onChange={e => setDateTo(e.target.value)} className="im-date-input" />
              </label>
            </div>
          )}
        </div>

        {plan && (
          <div className="im-section">
            <div className="im-label">
              <span>Plan</span>
              <span className="im-label-hint">~{livePlan ? livePlan.estimate_candidates : estimate} candidates · ~{livePlan ? livePlan.estimate_summarise : Math.min(estimate, 40)} will be summarised</span>
            </div>
            <div className="im-plan">
              {plan.map((p, i) => (
                <div key={i} className="im-plan-row">
                  <span className="im-plan-idx">{String(i + 1).padStart(2, "0")}</span>
                  <span className="im-plan-step">{p.step}</span>
                  <span className="im-plan-note">{p.note}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {history.length > 0 && (
          <div className="im-section">
            <div className="im-label"><span>Recent</span></div>
            <div className="im-history">
              {history.map((h, i) => (
                <button key={i} className="im-hist" onClick={() => setInterests(h)}>
                  <span className="im-hist-count">{h.length}</span>
                  <span className="im-hist-text">{h.slice(0, 3).join(" · ")}{h.length > 3 ? ` +${h.length - 3}` : ""}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="im-foot">
          <label className="im-check">
            <input type="checkbox" defaultChecked /> Save as watch
          </label>
          <div className="im-foot-actions">
            <button className="ghost" onClick={onClose}>Cancel</button>
            <button className="primary" onClick={run} disabled={!interests.length || pulling}>
              {pulling ? "Pulling…" : `Pull · ${interests.length || 0} interest${interests.length === 1 ? "" : "s"}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── IngestResultPanel ─────────────────────────────────────────────────────────

const SOURCE_COLORS: Record<string, string> = {
  arxiv: "var(--ink-4)", biorxiv: "#a8c97a", pubmed: "#e07060",
  openreview: "#b39ddb", wikipedia: "#4db6ac", web: "#7b9fd4",
};

export interface IngestResult {
  running: boolean;
  found: number;
  scanned: number;
  duration_ms?: number;
  papers: Paper[];
  errors: string[];
}

export interface IngestResultPanelProps {
  result: IngestResult;
  onClose: () => void;
  onOpenPaper: (id: string) => void;
}

export function IngestResultPanel({ result, onClose, onOpenPaper }: IngestResultPanelProps) {
  const { running, found, scanned, duration_ms, papers, errors } = result;
  const [minimized, setMinimized] = useState(false);

  const bySource: Record<string, number> = {};
  papers.forEach(p => {
    const src = (p.source || "arxiv").toLowerCase();
    bySource[src] = (bySource[src] || 0) + 1;
  });
  const secs = duration_ms ? (duration_ms / 1000).toFixed(1) : null;

  return (
    <div className="irp-wrap">
      <div className={"irp" + (minimized ? " irp-minimized" : "")}>
        <div className="irp-head">
          <div className="irp-head-left">
            <span className="irp-kicker">
              {running ? <><span className="irp-spinner" /> Ingesting…</> : "Ingest complete"}
            </span>
            <span className="irp-stat-row">
              <span className="irp-found">{found}</span>
              <span className="irp-stat-label">
                {running ? " new so far" : ` new · ${scanned} scanned${secs ? ` · ${secs}s` : ""}`}
              </span>
            </span>
          </div>
          <div className="irp-head-btns">
            <button className="irp-close" onClick={() => setMinimized(m => !m)} title={minimized ? "Expand" : "Minimise"}>
              {minimized ? "▲" : "▼"}
            </button>
            <button className="irp-close" onClick={onClose} title="Dismiss">✕</button>
          </div>
        </div>

        {!minimized && (
          <>
            {Object.keys(bySource).length > 0 && (
              <div className="irp-sources">
                {Object.entries(bySource).map(([src, n]) => (
                  <span key={src} className="irp-src-pill"
                        style={{ color: SOURCE_COLORS[src] || "var(--ink-4)", borderColor: SOURCE_COLORS[src] || "var(--rule)" }}>
                    {src.toUpperCase()} · {n}
                  </span>
                ))}
              </div>
            )}
            {errors.length > 0 && (
              <div className="irp-errors">
                {errors.map((e, i) => <div key={i} className="irp-error-row">⚠ {e}</div>)}
              </div>
            )}
            {papers.length === 0 && !running && errors.length === 0 && (
              <div className="irp-empty">No new papers found. Try broader interests or a longer date window.</div>
            )}
            {running && papers.length === 0 && (
              <div className="irp-empty">Fetching papers…</div>
            )}
            {papers.length > 0 && (
              <div className="irp-list">
                {papers.map(p => {
                  const src = (p.source || "arxiv").toLowerCase();
                  return (
                    <button key={p.id} className="irp-row" onClick={() => { onOpenPaper(p.id); onClose(); }}>
                      <span className="irp-src-badge" style={{ color: SOURCE_COLORS[src] || "var(--ink-4)" }}>
                        {src.toUpperCase()}
                      </span>
                      <span className="irp-title">{p.title}</span>
                      <span className="irp-go">↗</span>
                    </button>
                  );
                })}
              </div>
            )}
            {!running && (
              <div className="irp-foot">
                <button className="ghost" onClick={onClose}>Dismiss</button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
