import { useState, useEffect, useReducer, useRef, useCallback } from "react";
import { api, API_BASE } from "../api";
import type { Paper, Traversal, Note } from "../types";
import { nodeColor, CitationGraph } from "./CitationGraph";
import { Md, Reader } from "./PaperReader";

// ── Module-level mutable state ────────────────────────────────────────────────

const _htmlState: Record<string, string> = {};

const _JOURNEY_KEY  = "rs:journey_v1";
const _ACTIONS_KEY  = "rs:journey_actions_v1";

interface JourneyPaper {
  id: string;
  title: string;
  venue?: string;
  source?: string;
  date?: string;
  topics: string[];
  is_downloaded: boolean;
}

interface JourneyStep {
  id: string;
  paper: JourneyPaper;
  visitedAt: Date;
  revisitCount: number;
}

function _loadJourney(): JourneyStep[] {
  try {
    const raw = localStorage.getItem(_JOURNEY_KEY);
    if (raw) return (JSON.parse(raw) as JourneyStep[]).map(s => ({ ...s, visitedAt: new Date(s.visitedAt) }));
  } catch { /* ignore */ }
  return [];
}

function _loadActions(): Record<string, Set<string>> {
  try {
    const raw = localStorage.getItem(_ACTIONS_KEY);
    if (raw) {
      const obj = JSON.parse(raw) as Record<string, string[]>;
      const out: Record<string, Set<string>> = {};
      for (const [k, v] of Object.entries(obj)) out[k] = new Set(v);
      return out;
    }
  } catch { /* ignore */ }
  return {};
}

function _saveJourney() {
  try {
    localStorage.setItem(_JOURNEY_KEY, JSON.stringify(_journey.map(s => ({ ...s, visitedAt: s.visitedAt.toISOString() }))));
  } catch { /* ignore */ }
}

function _saveActions() {
  try {
    const obj: Record<string, string[]> = {};
    for (const [k, v] of Object.entries(_journeyActions)) obj[k] = [...v];
    localStorage.setItem(_ACTIONS_KEY, JSON.stringify(obj));
  } catch { /* ignore */ }
}

const _journey: JourneyStep[] = _loadJourney();
const _journeyActions: Record<string, Set<string>> = _loadActions();

function _addJourneyAction(paperId: string, action: string) {
  if (!paperId || !action) return;
  if (!_journeyActions[paperId]) _journeyActions[paperId] = new Set();
  _journeyActions[paperId].add(action);
  _saveActions();
}

window.addEventListener("rs:journey-action", (ev) => {
  const detail = (ev as CustomEvent<{ paperId?: string; action?: string }>).detail || {};
  if (detail.paperId && detail.action) _addJourneyAction(detail.paperId, detail.action);
});

window.addEventListener("rs:ocr-complete", (ev) => {
  const detail = (ev as CustomEvent<{ paperId?: string }>).detail;
  if (detail?.paperId) _addJourneyAction(detail.paperId, "ocr");
});

interface DiscoverState {
  n: number;
  running: boolean;
  result: string;
  candidates: Paper[];
  controller: { abort: () => void } | null;
  _tick: (() => void) | null;
}

const _discoverState: DiscoverState = {
  n: 10,
  running: false,
  result: "",
  candidates: [],
  controller: null,
  _tick: null,
};

// ── DiscoverPanel ─────────────────────────────────────────────────────────────

export function DiscoverPanel({ paper }: { paper: Paper | null }) {
  const [, tick] = useReducer((x: number) => x + 1, 0);

  useEffect(() => {
    _discoverState._tick = tick;
    return () => { _discoverState._tick = null; };
  }, []);

  const { n, running, result, candidates } = _discoverState;

  const setN = (v: number) => { _discoverState.n = v; tick(); };

  if (!paper) return (
    <div style={{ padding: "24px 0", color: "var(--ink-4)", fontSize: 13 }}>
      Select a paper first, then use Discover to find unexpected connections.
    </div>
  );

  const run = () => {
    _discoverState.running = true;
    _discoverState.result = "";
    _discoverState.candidates = [];
    tick();
    _discoverState.controller = api.streamDiscover(paper.id, n, {
      onCandidates: (papers: Paper[]) => {
        _discoverState.candidates = papers;
        _discoverState._tick?.();
      },
      onContent: (d: string) => {
        _discoverState.result += d;
        _discoverState._tick?.();
      },
      onDone: () => {
        _discoverState.running = false;
        _discoverState.controller = null;
        _discoverState._tick?.();
      },
      onError: (e: string) => {
        _discoverState.result += `\n\n[Error: ${e}]`;
        _discoverState.running = false;
        _discoverState.controller = null;
        _discoverState._tick?.();
      },
    });
  };

  const cancel = () => {
    _discoverState.controller?.abort();
    _discoverState.controller = null;
    _discoverState.running = false;
    tick();
  };

  return (
    <div style={{ padding: "20px 0" }}>
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontWeight: 600, fontSize: 13, color: "var(--ink-1)", marginBottom: 4 }}>
          Discover: {paper.title.length > 60 ? paper.title.slice(0, 60) + "…" : paper.title}
        </div>
        <div style={{ fontSize: 12, color: "var(--ink-3)", lineHeight: 1.5, marginBottom: 12 }}>
          Sample random papers and find non-obvious connections.
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
          <label style={{ fontSize: 11, color: "var(--ink-3)", whiteSpace: "nowrap", letterSpacing: "0.04em" }}>
            Probe
          </label>
          <input type="number" min="5" max="50" value={n}
            onChange={e => setN(Math.max(5, Math.min(50, parseInt(e.target.value) || 10)))}
            style={{ width: 52, padding: "3px 6px", fontSize: 12, fontFamily: "var(--font-mono)",
                     background: "var(--bg-2)", border: "1px solid var(--rule-2)", borderRadius: 4,
                     color: "var(--ink-1)", textAlign: "center" }} />
          <span style={{ fontSize: 11, color: "var(--ink-4)" }}>papers</span>
          <button
            onClick={running ? cancel : run}
            style={{ marginLeft: "auto", padding: "5px 16px",
                     background: running ? "transparent" : "var(--rust)",
                     color: running ? "var(--rust)" : "#fff",
                     border: running ? "1px solid var(--rust)" : "none",
                     borderRadius: 4, cursor: "pointer", fontSize: 12, whiteSpace: "nowrap",
                     fontFamily: "var(--font-display)" }}>
            {running
              ? <><span style={{ display: "inline-block", width: 8, height: 8, border: "1.5px solid var(--rust)", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite", marginRight: 5 }} />Cancel</>
              : "→ Discover"}
          </button>
        </div>
      </div>

      {candidates.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.07em", color: "var(--ink-4)", marginBottom: 6 }}>
            PROBING {candidates.length} PAPERS
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            {candidates.map((c, i) => (
              <div key={c.id} style={{ display: "flex", gap: 7, alignItems: "baseline" }}>
                <span style={{ fontSize: 9, fontFamily: "var(--font-mono)", color: "var(--ink-5)", minWidth: 18, textAlign: "right" }}>
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span style={{ fontSize: 11, color: "var(--ink-3)", lineHeight: 1.4 }}>
                  {c.title.length > 72 ? c.title.slice(0, 72) + "…" : c.title}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {running && !result && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--ink-4)", fontSize: 12, padding: "4px 0 12px" }}>
          <span style={{ display: "inline-block", width: 10, height: 10, border: "1.5px solid var(--rust)", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
          LLM is exploring connections…
        </div>
      )}

      {result && (
        <div style={{ background: "var(--bg-2)", border: "1px solid var(--rule)", borderRadius: 6,
                      padding: "14px 18px", fontSize: 12, lineHeight: 1.8, color: "var(--ink-1)" }}>
          <Md text={result} />
        </div>
      )}

      {!result && !running && (
        <div style={{ color: "var(--ink-4)", fontSize: 12, fontStyle: "italic", padding: "8px 0" }}>
          Results will appear here. Requires LLM configured in ⚙ Settings.
        </div>
      )}
    </div>
  );
}

// ── Notes ─────────────────────────────────────────────────────────────────────

interface NoteColor {
  bg: string;
  border: string;
}


interface StickyNoteProps {
  note: Note;
  col: NoteColor;
  isEditing: boolean;
  isAgent: boolean;
  editText: string;
  setEditText: (v: string) => void;
  onSave: () => void;
  onCancelEdit: () => void;
  onStartEdit: () => void;
  onDelete: () => void;
  maxHeightPx: number;
  fmtDate: (iso: string) => string;
}

function StickyNote({ note, col, isEditing, isAgent, editText, setEditText, onSave, onCancelEdit, onStartEdit, onDelete, maxHeightPx, fmtDate }: StickyNoteProps) {
  const [expanded, setExpanded] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const [overflows, setOverflows] = useState(false);

  useEffect(() => {
    if (contentRef.current) {
      setOverflows(contentRef.current.scrollHeight > maxHeightPx + 4);
    }
  }, [note.content, maxHeightPx]);

  return (
    <div style={{
      background: col.bg,
      border: `1px solid ${col.border}`,
      borderRadius: "var(--r-md)",
      boxShadow: "0 1px 4px rgba(36,28,18,0.08), 0 0 0 1px rgba(36,28,18,0.03)",
      display: "flex",
      flexDirection: "column",
      overflow: "hidden",
      transition: "box-shadow 0.15s",
    }}>
      <div style={{ height: 4, background: isAgent ? "var(--rust)" : col.border, flexShrink: 0 }} />
      <div style={{ padding: "10px 12px 10px" }}>
        {isEditing ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <textarea
              autoFocus
              value={editText}
              onChange={e => setEditText(e.target.value)}
              onKeyDown={e => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); onSave(); }
                if (e.key === "Escape") onCancelEdit();
              }}
              style={{
                width: "100%", minHeight: 72, resize: "none", fontSize: 12,
                background: "var(--bg)", border: "1px solid var(--rule-2)",
                outline: "none", borderRadius: "var(--r-sm)", padding: "6px 8px",
                color: "var(--ink)", fontFamily: "inherit", lineHeight: 1.6,
                boxSizing: "border-box",
              }}
            />
            <div style={{ display: "flex", gap: 6 }}>
              <button className="ghost" onClick={onSave} style={{ fontSize: 10, padding: "2px 10px" }}>Save</button>
              <button className="ghost" onClick={onCancelEdit} style={{ fontSize: 10, padding: "2px 10px" }}>Cancel</button>
            </div>
          </div>
        ) : (
          <>
            {isAgent && (
              <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: "0.06em", color: "var(--rust)", marginBottom: 5, textTransform: "uppercase" }}>
                ◆ Agent
              </div>
            )}
            <div
              ref={contentRef}
              style={{
                fontSize: 12, lineHeight: 1.65, color: "var(--ink-2)",
                whiteSpace: "pre-wrap", wordBreak: "break-word",
                maxHeight: expanded ? "none" : maxHeightPx,
                overflow: "hidden",
              }}
            >
              {note.content}
            </div>
            {overflows && (
              <button
                onClick={() => setExpanded(x => !x)}
                style={{ marginTop: 4, fontSize: 10, color: "var(--ink-4)", background: "none", border: "none", cursor: "pointer", padding: 0, fontFamily: "inherit" }}
              >
                {expanded ? "show less" : "show more…"}
              </button>
            )}
            <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 4, borderTop: "1px solid var(--rule)", paddingTop: 6 }}>
              <span style={{ fontSize: 9, color: "var(--ink-4)", flex: 1, fontVariantNumeric: "tabular-nums" }}>
                {fmtDate(note.created_at)}
              </span>
              <button onClick={onStartEdit} title="Edit" style={{ fontSize: 11, background: "none", border: "none", cursor: "pointer", color: "var(--ink-4)", padding: "0 3px" }}>✎</button>
              <button onClick={onDelete} title="Delete" style={{ fontSize: 11, background: "none", border: "none", cursor: "pointer", color: "var(--rust)", opacity: 0.65, padding: "0 3px" }}>✕</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function NotesPanel({ paper }: { paper: Paper | null }) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const reload = () => {
    if (!paper) return;
    api.fetchNotes(paper.id).then(ns => setNotes(ns)).catch(() => {});
  };

  useEffect(() => {
    setNotes([]);
    setDraft("");
    setEditId(null);
    reload();
  }, [paper?.id]);

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ paperId?: string }>).detail;
      if (!detail?.paperId || detail.paperId === paper?.id) reload();
    };
    window.addEventListener("rs:note-added", handler);
    return () => window.removeEventListener("rs:note-added", handler);
  }, [paper?.id]);

  const submit = async () => {
    const text = draft.trim();
    if (!text || !paper || saving) return;
    setSaving(true);
    try {
      const note = await api.createNote(paper.id, text);
      setNotes(prev => [...prev, note]);
      setDraft("");
    } catch { /* ignore */ }
    setSaving(false);
  };

  const startEdit = (note: Note) => {
    setEditId(note.id);
    setEditText(note.content);
  };

  const saveEdit = async (note: Note) => {
    const text = editText.trim();
    if (!text) return;
    try {
      const updated = await api.updateNote(paper!.id, note.id, text);
      setNotes(prev => prev.map(n => n.id === note.id ? updated : n));
    } catch { /* ignore */ }
    setEditId(null);
  };

  const del = async (note: Note) => {
    await api.deleteNote(paper!.id, note.id);
    setNotes(prev => prev.filter(n => n.id !== note.id));
  };

  const handleKey = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); void submit(); }
  };

  if (!paper) return (
    <div style={{ padding: 60, color: "var(--ink-4)", textAlign: "center", fontSize: 13 }}>
      Select a paper to take notes.
    </div>
  );

  const fmtDate = (iso: string) => {
    try { return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
    catch { return iso?.slice(0, 16) || ""; }
  };

  const isDark = typeof document !== "undefined" &&
    document.documentElement.getAttribute("data-theme") === "dark";

  const STICKY_LIGHT: NoteColor[] = [
    { bg: "#FAF5E6", border: "#D9CBA8" },
    { bg: "#F5EDD6", border: "#D2C49A" },
    { bg: "#EEE8DA", border: "#C9BFA0" },
    { bg: "#F2EBE0", border: "#CDBFA3" },
    { bg: "#EBE8E0", border: "#C6BEB0" },
    { bg: "#F0EBE3", border: "#CCBFB0" },
  ];
  const STICKY_DARK: NoteColor[] = [
    { bg: "#2A2620", border: "#3E3830" },
    { bg: "#252218", border: "#3A3326" },
    { bg: "#272420", border: "#3C3630" },
    { bg: "#2C2318", border: "#413420" },
    { bg: "#222220", border: "#363630" },
    { bg: "#26221C", border: "#3A3428" },
  ];
  const AGENT_COL_LIGHT: NoteColor = { bg: "#F5ECE6", border: "#C89080" };
  const AGENT_COL_DARK: NoteColor  = { bg: "#2E1F1A", border: "#6B3020" };

  const noteColorFn = (note: Note): NoteColor => {
    if (note.source === "agent") return isDark ? AGENT_COL_DARK : AGENT_COL_LIGHT;
    const seed = typeof note.id === "number" ? note.id : 0;
    return (isDark ? STICKY_DARK : STICKY_LIGHT)[seed % 6];
  };

  const NOTE_COLLAPSE_LINES = 6;

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", background: "var(--bg-1)" }}>
      <div style={{ padding: "12px 20px 10px", borderBottom: "1px solid var(--rule)", flexShrink: 0, background: "var(--bg)" }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)", marginBottom: 2 }}>Notes</div>
        <div style={{ fontSize: 11, color: "var(--ink-4)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {paper.title}
        </div>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "20px 16px 12px" }}>
        {notes.length === 0 && (
          <div style={{ color: "var(--ink-4)", fontSize: 12, fontStyle: "italic", paddingTop: 24, textAlign: "center" }}>
            No notes yet. Add one below, or ask the agent.
          </div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(168px, 1fr))", gap: 16, alignItems: "start" }}>
          {notes.map((note) => {
            const col = noteColorFn(note);
            const isEditing = editId === note.id;
            const isAgent = note.source === "agent";
            const lineHeight = 1.65;
            const fontSize = 12;
            const maxHeightPx = NOTE_COLLAPSE_LINES * fontSize * lineHeight;
            return (
              <StickyNote
                key={note.id}
                note={note}
                col={col}
                isEditing={isEditing}
                isAgent={isAgent}
                editText={editText}
                setEditText={setEditText}
                onSave={() => void saveEdit(note)}
                onCancelEdit={() => setEditId(null)}
                onStartEdit={() => startEdit(note)}
                onDelete={() => void del(note)}
                maxHeightPx={maxHeightPx}
                fmtDate={fmtDate}
              />
            );
          })}
        </div>
      </div>

      <div style={{ flexShrink: 0, borderTop: "1px solid var(--rule)", padding: "12px 16px 16px", background: "var(--bg)" }}>
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Add a note… (⌘↵ to save)"
          style={{
            width: "100%", minHeight: 72, resize: "vertical", fontSize: 13,
            background: "var(--bg-1)", border: "1px solid var(--rule-2)",
            borderRadius: "var(--r-md)", padding: "8px 10px",
            color: "var(--ink)", fontFamily: "inherit", lineHeight: 1.55,
            boxSizing: "border-box", outline: "none",
            transition: "border-color 0.12s",
          }}
          onFocus={e => { e.target.style.borderColor = "var(--ink-4)"; }}
          onBlur={e => { e.target.style.borderColor = "var(--rule-2)"; }}
        />
        <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 10, marginTop: 8 }}>
          <span style={{ fontSize: 10, color: "var(--ink-5)" }}>⌘↵</span>
          <button
            className="ghost"
            onClick={() => void submit()}
            disabled={saving || !draft.trim()}
            style={{ fontSize: 12, padding: "4px 14px", opacity: draft.trim() ? 1 : 0.4 }}
          >
            {saving ? "Saving…" : "Add note"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Research Journey ──────────────────────────────────────────────────────────

function JourneyBadge({ label, title, color }: { label: string; title: string; color?: string }) {
  return (
    <span title={title} style={{
      fontSize: 9, padding: "1px 4px", borderRadius: 3,
      background: "var(--bg-3)", color: color || "var(--ink-3)",
      fontFamily: "var(--font-mono)", lineHeight: 1.6, whiteSpace: "nowrap",
    }}>{label}</span>
  );
}

function JourneyView({ activeId, onSelect }: { activeId: string | null; onSelect: (id: string) => void }) {
  const [, tick] = useReducer((x: number) => x + 1, 0);
  const [chatCounts, setChatCounts] = useState<Record<string, number>>({});

  useEffect(() => {
    fetch(`${API_BASE}/users/me/chats`)
      .then(r => r.ok ? r.json() : [])
      .then((chats: Array<{ paper_id?: string; message_count?: number }>) => {
        const counts: Record<string, number> = {};
        chats.forEach(c => { if (c.paper_id && c.message_count) counts[c.paper_id] = (counts[c.paper_id] || 0) + c.message_count; });
        setChatCounts(counts);
      }).catch(() => {});

    const rerender = () => tick();
    window.addEventListener("rs:journey-action", rerender);
    window.addEventListener("rs:ocr-complete", rerender);
    return () => {
      window.removeEventListener("rs:journey-action", rerender);
      window.removeEventListener("rs:ocr-complete", rerender);
    };
  }, []);

  const clearJourney = () => {
    _journey.length = 0;
    try { localStorage.removeItem(_JOURNEY_KEY); localStorage.removeItem(_ACTIONS_KEY); } catch { /* ignore */ }
    tick();
  };

  if (_journey.length === 0) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
                    height: "100%", gap: 12, color: "var(--ink-4)" }}>
        <div style={{ fontSize: 36, opacity: 0.3 }}>◎</div>
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--ink-2)" }}>No journey yet</div>
        <div style={{ fontSize: 12, maxWidth: 360, textAlign: "center", lineHeight: 1.65 }}>
          Navigate papers using the Read or Graph tab. Your research path will appear here as a causal chain — left to right over time.
        </div>
      </div>
    );
  }

  const now = new Date();

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 20px 8px",
                    borderBottom: "1px solid var(--rule)", flexShrink: 0 }}>
        <div style={{ fontWeight: 600, fontSize: 13, color: "var(--ink-1)" }}>Research Journey</div>
        <div style={{ fontSize: 11, color: "var(--ink-4)" }}>{_journey.length} papers</div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 10, fontSize: 10, color: "var(--ink-4)" }}>
          <span><span style={{ color: "var(--rust)" }}>∑</span> summary</span>
          <span><span style={{ color: "#5a8af0" }}>◈</span> chat</span>
          <span><span style={{ color: "#3a8a6a" }}>◉</span> semantic</span>
          <span><span style={{ color: "var(--ink-3)" }}>⌖</span> ocr</span>
        </div>
        <button className="ghost" style={{ fontSize: 10, padding: "2px 8px", marginLeft: 8 }} onClick={clearJourney}>✕ Clear</button>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "20px 20px 16px" }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "14px 10px", alignItems: "flex-start" }}>
          {_journey.map((step, i) => {
            const acts      = _journeyActions[step.id] || new Set<string>();
            const chatCount = chatCounts[step.id] || 0;
            const color     = nodeColor(step.paper.topics || []);
            const isActive  = step.id === activeId;
            const isToday   = step.visitedAt.toDateString() === now.toDateString();
            const timeLabel = isToday
              ? step.visitedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
              : step.visitedAt.toLocaleDateString([], { month: "short", day: "numeric" }) + " · " +
                step.visitedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
            const prevStep = i > 0 ? _journey[i - 1] : null;
            const isNewDay = prevStep && step.visitedAt.toDateString() !== prevStep.visitedAt.toDateString();

            return (
              <div key={step.id + "_" + i} style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
                {isNewDay && (
                  <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: "0.07em", color: "var(--rust)",
                                fontFamily: "var(--font-mono)", marginBottom: 4, alignSelf: "flex-start",
                                paddingLeft: 2, opacity: 0.7 }}>
                    ↳ {step.visitedAt.toLocaleDateString([], { month: "short", day: "numeric" }).toUpperCase()}
                  </div>
                )}
                <div onClick={() => onSelect(step.id)} style={{
                  width: 168, cursor: "pointer", borderRadius: 6, overflow: "hidden",
                  border: `1.5px solid ${isActive ? "var(--rust)" : "var(--rule)"}`,
                  background: isActive ? "var(--bg-2)" : "var(--bg)",
                  boxShadow: isActive ? "0 0 0 3px rgba(194,113,42,0.12)" : "none",
                  transition: "border-color 0.15s, box-shadow 0.15s",
                }}>
                  <div style={{ height: 3, background: color }} />
                  <div style={{ padding: "7px 9px 8px" }}>
                    <div style={{ display: "flex", alignItems: "flex-start", gap: 4, marginBottom: 3 }}>
                      <div style={{ fontSize: 11, fontWeight: isActive ? 600 : 400,
                                    color: isActive ? "var(--ink-1)" : "var(--ink-2)",
                                    lineHeight: 1.35, flex: 1 }}>
                        {step.paper.title.length > 52 ? step.paper.title.slice(0, 52) + "…" : step.paper.title}
                      </div>
                      <span style={{ fontSize: 9, fontFamily: "var(--font-mono)", color: "var(--ink-5)", flexShrink: 0, paddingTop: 1 }}>
                        {String(i + 1).padStart(2, "0")}
                      </span>
                    </div>
                    <div style={{ fontSize: 9, color: "var(--ink-4)", marginBottom: 5, lineHeight: 1.4 }}>
                      {(step.paper.venue || step.paper.source || "").slice(0, 24)} · {(step.paper.date || "").slice(0, 7)}
                    </div>
                    <div style={{ display: "flex", gap: 3, flexWrap: "wrap", minHeight: 16 }}>
                      {acts.has("summarized")                              && <JourneyBadge label="∑"  title="Summarized"        color="var(--rust)" />}
                      {chatCount > 0                                        && <JourneyBadge label={`◈${chatCount}`} title={`${chatCount} chat messages`} color="#5a8af0" />}
                      {(step.paper.is_downloaded || acts.has("pdf"))        && <JourneyBadge label="⬇"  title="PDF downloaded"    color="var(--ink-3)" />}
                      {acts.has("ocr")                                      && <JourneyBadge label="⌖"  title="OCR extracted"     color="var(--ink-3)" />}
                      {acts.has("semantic")                                 && <JourneyBadge label="◉"  title="Semantic explored"  color="#3a8a6a" />}
                      {step.revisitCount > 0                                && <JourneyBadge label={`×${step.revisitCount + 1}`} title={`Revisited ${step.revisitCount} times`} color="var(--ink-4)" />}
                    </div>
                  </div>
                </div>
                <div style={{ fontSize: 9, color: "var(--ink-5)", marginTop: 4, fontFamily: "var(--font-mono)", textAlign: "center" }}>
                  {timeLabel}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── Wikipedia / Web article viewer ────────────────────────────────────────────

interface WikiSectionData {
  level: number;
  title: string | null;
  body: string;
}

function _parseWikiSections(text: string): WikiSectionData[] {
  const lines = text.split("\n");
  const sections: WikiSectionData[] = [];
  let cur: { level: number; title: string | null; lines: string[] } = { level: 0, title: null, lines: [] };
  for (const line of lines) {
    const m = line.match(/^(={2,4})\s*(.+?)\s*\1\s*$/);
    if (m) {
      const body = cur.lines.join("\n").trim();
      if (body || cur.title !== null) sections.push({ level: cur.level, title: cur.title, body });
      cur = { level: m[1].length, title: m[2], lines: [] };
    } else {
      cur.lines.push(line);
    }
  }
  const body = cur.lines.join("\n").trim();
  if (body || cur.title !== null) sections.push({ level: cur.level, title: cur.title, body });
  return sections;
}

function WikiSectionView({ section }: { section: WikiSectionData }) {
  const [open, setOpen] = useState(section.level <= 2);
  const Tag = (section.level === 2 ? "h3" : "h4") as "h3" | "h4";
  return (
    <div style={{ marginBottom: 16, borderBottom: "1px solid var(--rule)", paddingBottom: 10 }}>
      {section.title && (
        <div
          style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 6, marginBottom: open ? 8 : 0 }}
          onClick={() => setOpen(o => !o)}
        >
          <span style={{ fontSize: 9, color: "var(--ink-4)", userSelect: "none" }}>{open ? "▾" : "▸"}</span>
          <Tag style={{ margin: 0, fontSize: section.level === 2 ? 15 : 13, fontWeight: 600, color: "var(--ink-1)" }}>
            {section.title}
          </Tag>
        </div>
      )}
      {open && (
        <div style={{ fontSize: 12, lineHeight: 1.7, color: "var(--ink-2)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {section.body}
        </div>
      )}
    </div>
  );
}

interface WikiData {
  text: string;
  page_url?: string;
  char_count: number;
}

function WikiArticleViewer({ paper }: { paper: Paper | null }) {
  const [data, setData] = useState<WikiData | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!paper) return;
    setLoading(true); setErr(null); setData(null);
    fetch(`${API_BASE}/papers/${encodeURIComponent(paper.id)}/fulltext`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: WikiData) => { setData(d); setLoading(false); })
      .catch((e: unknown) => { setErr(`Could not load article (${String(e)})`); setLoading(false); });
  }, [paper?.id]);

  if (!paper) return null;

  const isWeb = paper.id?.startsWith("web:");
  const lang = paper.id?.split(":")?.[1] || "en";
  const fallbackUrl = isWeb
    ? null
    : `https://en.wikipedia.org/wiki/${encodeURIComponent((paper.title || "").replace(/ /g, "_"))}`;
  const pageUrl = data?.page_url || fallbackUrl;
  const dotColor = isWeb ? "#7b9fd4" : "#4db6ac";
  const sourceLabel = isWeb ? "Web" : `Wikipedia (${lang})`;
  const linkLabel = isWeb ? "↗ Open" : "↗ Wikipedia";

  const centerStyle: React.CSSProperties = { display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 12, color: "var(--ink-4)", fontSize: 13 };

  if (loading) return <div style={centerStyle}><span style={{ fontSize: 22 }}>⌛</span>Loading article…</div>;
  if (err)     return <div style={centerStyle}><span style={{ fontSize: 22 }}>⚠</span>{err}</div>;

  const sections = _parseWikiSections(data?.text || "");

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 10, padding: "5px 16px",
        borderBottom: "1px solid var(--rule)", background: "var(--bg-2)",
        fontSize: 11, color: "var(--ink-4)", flexShrink: 0,
      }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: dotColor, flexShrink: 0 }} />
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {sourceLabel} · {paper.title}
        </span>
        <span style={{ fontFamily: "var(--font-mono)", color: "var(--ink-4)" }}>
          {data ? `${(data.char_count / 1000).toFixed(1)}k chars` : ""}
        </span>
        {pageUrl && <a href={pageUrl} target="_blank" rel="noopener noreferrer"
           style={{ color: "var(--rust)", textDecoration: "none", flexShrink: 0 }}>{linkLabel}</a>}
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: "20px 28px 40px" }}>
        {sections.map((sec, i) => <WikiSectionView key={i} section={sec} />)}
      </div>
    </div>
  );
}

// ── PDF Viewer ────────────────────────────────────────────────────────────────

function PdfViewer({ paper }: { paper: Paper | null }) {
  const [downloading, setDownloading] = useState(false);
  const [justDownloaded, setJustDownloaded] = useState(false);

  if (!paper) return null;

  const pdfSrc = `${API_BASE}/papers/${encodeURIComponent(paper.id)}/pdf.pdf`;
  const isLocal = paper.is_downloaded || justDownloaded;

  const triggerDownload = () => {
    setDownloading(true);
    fetch(`${API_BASE}/papers/${encodeURIComponent(paper.id)}/ingest`, { method: "POST" })
      .then(() => {
        const poll = setInterval(() => {
          fetch(`${API_BASE}/papers/${encodeURIComponent(paper.id)}/cache`)
            .then(r => r.ok ? r.json() : null)
            .then((cache: { pdf?: boolean } | null) => {
              if (cache && cache.pdf) {
                clearInterval(poll);
                setDownloading(false);
                setJustDownloaded(true);
                window.dispatchEvent(new CustomEvent("rs:refreshPapers"));
              }
            }).catch(() => {});
        }, 2000);
        setTimeout(() => { clearInterval(poll); setDownloading(false); }, 120000);
      })
      .catch(() => setDownloading(false));
  };

  if (!isLocal && !paper.pdf_url) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 14, color: "var(--ink-4)" }}>
        <div style={{ fontSize: 32 }}>∅</div>
        <div style={{ fontSize: 14, fontWeight: 600, color: "var(--ink-2)" }}>No PDF available</div>
        <div style={{ fontSize: 12, maxWidth: 320, textAlign: "center", lineHeight: 1.6, color: "var(--ink-3)" }}>
          This item has no associated PDF. Its full text is available via the agent's rag_query tool.
        </div>
      </div>
    );
  }

  if (!isLocal) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 16, color: "var(--ink-4)" }}>
        <div style={{ fontSize: 36 }}>⬇</div>
        <div style={{ fontSize: 14, fontWeight: 600, color: "var(--ink-2)" }}>PDF not yet downloaded</div>
        <div style={{ fontSize: 12, maxWidth: 340, textAlign: "center", lineHeight: 1.6 }}>
          Download the PDF locally to view it here inline. Once cached, it will be served through the local server and render in this panel.
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <button
            onClick={triggerDownload}
            disabled={downloading}
            style={{ padding: "8px 18px", background: "var(--rust)", color: "#fff", border: "none", borderRadius: 4, cursor: downloading ? "default" : "pointer", fontSize: 13 }}
          >
            {downloading ? "Downloading…" : "⬇ Download PDF"}
          </button>
          {paper.pdf_url && (
            <a href={paper.pdf_url} target="_blank" rel="noopener noreferrer"
               style={{ padding: "8px 18px", border: "1px solid var(--rule)", borderRadius: 4, textDecoration: "none", fontSize: 13, color: "var(--ink-2)" }}>
              ↗ Open externally
            </a>
          )}
        </div>
      </div>
    );
  }

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "5px 16px", borderBottom: "1px solid var(--rule)", background: "var(--bg-2)", fontSize: 11, color: "var(--ink-4)" }}>
        <span style={{ flex: 1 }}>Local PDF · {paper.id}</span>
        <a href={pdfSrc} target="_blank" rel="noopener noreferrer" style={{ color: "var(--rust)", textDecoration: "none" }}>↗ Open in tab</a>
      </div>
      <iframe
        key={paper.id}
        src={pdfSrc}
        style={{ flex: 1, border: "none", width: "100%" }}
        title={`PDF: ${paper.title}`}
      />
    </div>
  );
}

// ── HTML Picker ───────────────────────────────────────────────────────────────

interface WorkspaceFile {
  url: string;
  name: string;
  mtime: number;
  size: number;
}

function HtmlPicker({ paperId, currentUrl, onPick, onClose }: { paperId: string; currentUrl: string | null; onPick: (url: string) => void; onClose: () => void }) {
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [loading, setLoading] = useState(true);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.fetchWorkspaceFiles(paperId).then(raw => { setFiles(raw as WorkspaceFile[]); setLoading(false); });
  }, [paperId]);

  useEffect(() => {
    const handler = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose(); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  const fmt = (mtime: number) => {
    const d = new Date(mtime * 1000);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " " +
           d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  };

  return (
    <div ref={ref} style={{
      position: "absolute", top: 38, right: 0, zIndex: 50,
      background: "var(--bg)", border: "1px solid var(--rule-2)", borderRadius: 8,
      boxShadow: "0 6px 24px rgba(0,0,0,0.18)", width: 340, maxHeight: 380,
      display: "flex", flexDirection: "column", overflow: "hidden",
    }}>
      <div style={{ padding: "8px 14px 6px", borderBottom: "1px solid var(--rule)",
                    fontSize: 11, fontWeight: 600, color: "var(--ink-4)",
                    textTransform: "uppercase", letterSpacing: 0.7 }}>
        Workspace HTMLs
      </div>
      <div style={{ overflowY: "auto", flex: 1 }}>
        {loading && <div style={{ padding: "16px 14px", fontSize: 12, color: "var(--ink-4)" }}>Loading…</div>}
        {!loading && files.length === 0 && (
          <div style={{ padding: "16px 14px", fontSize: 12, color: "var(--ink-4)" }}>
            No HTML files yet. Ask the agent to create a dashboard.
          </div>
        )}
        {files.map(f => {
          const active = f.url === currentUrl;
          return (
            <div
              key={f.url}
              onClick={() => { onPick(f.url); onClose(); }}
              style={{
                padding: "9px 14px", cursor: "pointer",
                borderBottom: "1px solid var(--rule)",
                background: active ? "var(--bg-2)" : "transparent",
                borderLeft: active ? "2px solid var(--rust)" : "2px solid transparent",
                transition: "background 0.1s",
              }}
              onMouseEnter={e => { if (!active) (e.currentTarget as HTMLDivElement).style.background = "var(--bg-1)"; }}
              onMouseLeave={e => { if (!active) (e.currentTarget as HTMLDivElement).style.background = "transparent"; }}
            >
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-1)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {f.name}
              </div>
              <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 2, fontFamily: "var(--font-mono)" }}>
                {fmt(f.mtime)} · {(f.size / 1024).toFixed(1)} KB
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── CenterStage ───────────────────────────────────────────────────────────────

export interface CenterStageProps {
  mode: string;
  setMode: (m: string) => void;
  paper: Paper | null;
  papers: Paper[];
  edges: unknown[];
  onSelect: (id: string) => void;
  compareB?: Paper | null;
  rightCollapsed: boolean;
  toggleRight: () => void;
  traversal?: Traversal | null;
  onToggleBookmark?: (id: string) => void;
}

export function CenterStage({ mode, setMode, paper, onSelect, rightCollapsed, toggleRight, traversal, onToggleBookmark }: CenterStageProps) {
  const paperId = paper?.id || "__global__";

  const [dashboardUrl, setDashboardUrl] = useState<string | null>(() => _htmlState[paperId] || null);
  const [showPicker, setShowPicker] = useState(false);

  useEffect(() => {
    setDashboardUrl(_htmlState[paperId] || null);
    setShowPicker(false);
  }, [paperId]);

  const openUrl = useCallback((url: string) => {
    _htmlState[paperId] = url;
    setDashboardUrl(url);
  }, [paperId]);

  const clearUrl = useCallback(() => {
    delete _htmlState[paperId];
    setDashboardUrl(null);
  }, [paperId]);

  useEffect(() => {
    const handler = (ev: Event) => {
      const { url } = (ev as CustomEvent<{ url?: string }>).detail || {};
      if (url) {
        openUrl(url);
        setMode("html");
      }
    };
    document.addEventListener("rs:dashboard-created", handler);
    return () => document.removeEventListener("rs:dashboard-created", handler);
  }, [openUrl, setMode]);

  useEffect(() => {
    if (!paper) return;
    const last = _journey[_journey.length - 1];
    if (last && last.id === paper.id) {
      last.revisitCount = (last.revisitCount || 0) + 1;
    } else {
      if (_journey.length >= 200) _journey.splice(0, 1);
      _journey.push({
        id: paper.id,
        paper: { id: paper.id, title: paper.title, venue: paper.venue, source: paper.source,
                 date: paper.date, topics: paper.topics || [], is_downloaded: paper.is_downloaded || false },
        visitedAt: new Date(),
        revisitCount: 0,
      });
      _saveJourney();
    }
  }, [paper?.id]);

  useEffect(() => {
    if (paper?.is_downloaded) _addJourneyAction(paper.id, "pdf");
  }, [paper?.id, paper?.is_downloaded]);

  const isArticle = paper?.id?.startsWith("wikipedia:") || paper?.id?.startsWith("web:");

  return (
    <div className="pane center">
      <div className="center-head">
        <div className="tabs">
          <button className={"tab " + (mode === "read" ? "active" : "")} onClick={() => setMode("read")}>
            <span className="idx">01</span><span>Read</span>
          </button>
          <button className={"tab " + (mode === "pdf" ? "active" : "")} onClick={() => setMode("pdf")}>
            <span className="idx">02</span>
            <span>{isArticle ? "Article" : paper?.is_downloaded ? "PDF ●" : "PDF"}</span>
          </button>
          <button className={"tab " + (mode === "html" ? "active" : "")} onClick={() => setMode("html")}>
            <span className="idx">03</span><span>HTML</span>
          </button>
          <button className={"tab " + (mode === "graph" ? "active" : "")} onClick={() => setMode("graph")}>
            <span className="idx">04</span><span>Graph</span>
          </button>
          <button className={"tab " + (mode === "timeline" ? "active" : "")} onClick={() => setMode("timeline")}>
            <span className="idx">05</span><span>Journey</span>
          </button>
          <button className={"tab " + (mode === "notes" ? "active" : "")} onClick={() => setMode("notes")}>
            <span className="idx">06</span><span>Notes</span>
          </button>
        </div>
        <div className="grow" />
        <div className="actions">
          <button className="ghost" onClick={() => {
            if (!paper) return;
            if (isArticle) { setMode("pdf"); return; }
            window.open(api.pdfUrl(paper.id));
          }}>
            {isArticle ? "Article" : "↓ PDF"}
          </button>
          <button
            className="ghost"
            title={paper?.pinned ? "Remove bookmark" : "Bookmark this paper"}
            onClick={() => paper && onToggleBookmark?.(paper.id)}
            style={paper?.pinned ? { color: "var(--rust)" } : {}}
          >
            {paper?.pinned ? "★" : "☆"} Bookmark
          </button>
          <button className="ghost collapse-right" title={rightCollapsed ? "Show panel" : "Hide panel"} onClick={toggleRight}>
            {rightCollapsed ? "◀" : "▶"}
          </button>
        </div>
      </div>
      <div className="center-body" style={mode === "pdf" ? { padding: 0, overflow: "hidden" } : {}}>
        {mode === "read"     && <Reader paper={paper} />}
        {mode === "pdf"      && (isArticle ? <WikiArticleViewer paper={paper} /> : <PdfViewer paper={paper} />)}
        {mode === "html"     && (
          <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <div style={{
              display: "flex", alignItems: "center", gap: 8, padding: "5px 12px",
              borderBottom: "1px solid var(--rule)", background: "var(--bg-2)",
              fontSize: 11, color: "var(--ink-4)", flexShrink: 0, position: "relative",
            }}>
              {dashboardUrl ? (
                <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontFamily: "var(--font-mono)" }}>
                  {dashboardUrl}
                </span>
              ) : (
                <span style={{ flex: 1, fontStyle: "italic" }}>No dashboard open</span>
              )}
              {dashboardUrl && (
                <a href={dashboardUrl} target="_blank" rel="noopener noreferrer"
                   style={{ color: "var(--rust)", textDecoration: "none", flexShrink: 0 }}>
                  ↗ Open in tab
                </a>
              )}
              <button
                className="ghost"
                style={{ fontSize: 11, padding: "2px 8px", flexShrink: 0 }}
                onClick={() => setShowPicker(p => !p)}
                title="Browse workspace HTMLs"
              >
                ☰ Browse
              </button>
              {dashboardUrl && (
                <button className="ghost" style={{ fontSize: 11, padding: "2px 8px", flexShrink: 0 }}
                        onClick={clearUrl}>✕</button>
              )}
              {showPicker && (
                <HtmlPicker
                  paperId={paperId}
                  currentUrl={dashboardUrl}
                  onPick={openUrl}
                  onClose={() => setShowPicker(false)}
                />
              )}
            </div>
            {dashboardUrl ? (
              <iframe
                key={dashboardUrl}
                src={dashboardUrl}
                style={{ flex: 1, border: "none", width: "100%" }}
                title="Agent dashboard"
                sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"
              />
            ) : (
              <div style={{ flex: 1, display: "flex", flexDirection: "column",
                            alignItems: "center", justifyContent: "center",
                            gap: 12, color: "var(--ink-4)" }}>
                <div style={{ fontSize: 32 }}>⌘</div>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--ink-2)" }}>Agent dashboards</div>
                <div style={{ fontSize: 12, maxWidth: 340, textAlign: "center", lineHeight: 1.6 }}>
                  Ask the agent to create a dashboard, or use <strong>Browse</strong> to open an existing one.
                  Try: <em>"Create an HTML summary of this paper's methodology"</em>
                </div>
                <button
                  className="ghost"
                  style={{ fontSize: 12, padding: "5px 14px", marginTop: 4 }}
                  onClick={() => setShowPicker(p => !p)}
                >
                  ☰ Browse workspace HTMLs
                </button>
              </div>
            )}
          </div>
        )}
        {mode === "graph"    && <CitationGraph activeId={paper?.id ?? null} onSelect={onSelect} active={mode === "graph"} traversal={traversal} />}
        {mode === "timeline" && <JourneyView activeId={paper?.id ?? null} onSelect={onSelect} />}
        {mode === "notes"    && <NotesPanel paper={paper} />}
      </div>
    </div>
  );
}
