import { useState, useEffect, useRef } from "react";
import { marked } from "marked";
import renderMathInElement from "katex/contrib/auto-render";
import { api } from "../api";
import type { Paper } from "../types";

const _katexOpts = {
  delimiters: [
    { left: "$$", right: "$$", display: true },
    { left: "$",  right: "$",  display: false },
  ],
  throwOnError: false,
};

// ── MathText ──────────────────────────────────────────────────────────────────

export function MathText({ text }: { text?: string | null }) {
  const elRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (elRef.current) renderMathInElement(elRef.current, _katexOpts);
  }, [text]);
  if (!text) return null;
  return <span ref={elRef}>{text}</span>;
}

// ── Md ────────────────────────────────────────────────────────────────────────

export function Md({ text, inline = false }: { text?: string; inline?: boolean }) {
  const elRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (elRef.current) renderMathInElement(elRef.current, _katexOpts);
  }, [text]);
  if (!text) return null;
  const html = inline ? marked.parseInline(text) as string : marked.parse(text) as string;
  return <div ref={elRef as React.RefObject<HTMLDivElement>} className="md-body" dangerouslySetInnerHTML={{ __html: html }} />;
}

// ── RSection ──────────────────────────────────────────────────────────────────

interface RSectionProps {
  num: string;
  title: string;
  hint?: string | null;
  defaultOpen?: boolean;
  children?: React.ReactNode;
}

export function RSection({ num, title, hint, defaultOpen = false, children }: RSectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={"r-section " + (open ? "open" : "")}>
      <div className="rs-head" onClick={() => setOpen(!open)}>
        <div className="rs-num">{num}</div>
        <div className="rs-title">{title}</div>
        {hint && <div className="rs-hint">{hint}</div>}
        <div className="rs-chev">›</div>
      </div>
      <div className="rs-body">{children}</div>
    </div>
  );
}

// ── OcrTextViewer ─────────────────────────────────────────────────────────────

interface FulltextData {
  text: string;
  page_count: number;
  char_count: number;
}

function OcrTextViewer({ paper }: { paper: Paper }) {
  const [ft, setFt] = useState<FulltextData | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetched, setFetched] = useState(false);

  const load = () => {
    if (fetched) return;
    setLoading(true);
    api.fetchFulltext(paper.id)
      .then(d => { setFt(d as FulltextData); setFetched(true); setLoading(false); })
      .catch(() => { setFetched(true); setLoading(false); });
  };

  if (!ft && !fetched) {
    return (
      <RSection num="00" title="Full OCR text" hint="lazy">
        <div style={{ padding: "8px 0" }}>
          <button className="ghost" onClick={load} disabled={loading}>
            {loading ? "Loading…" : "⌖ Load full text"}
          </button>
        </div>
      </RSection>
    );
  }

  if (!ft) {
    return (
      <RSection num="00" title="Full OCR text" hint="error">
        <p style={{ color: "var(--rust)", fontSize: 12 }}>Failed to load OCR text.</p>
      </RSection>
    );
  }

  const pages = ft.text.split(/(?=--- Page \d+)/g).filter(Boolean);
  return (
    <RSection num="00" title="Full OCR text" hint={`${ft.page_count} pages · ${(ft.char_count / 1000).toFixed(0)} k chars`}>
      <div style={{ paddingBottom: 8 }}>
        {pages.map((pg, i) => {
          const headMatch = pg.match(/^--- Page (\d+) ---\n?/);
          const pageNum = headMatch ? headMatch[1] : String(i + 1);
          const body = headMatch ? pg.slice(headMatch[0].length) : pg;
          return (
            <RSection key={i} num={pageNum.padStart(2, "0")} title={`Page ${pageNum}`}>
              <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 11, lineHeight: 1.55, margin: 0 }}>{body.trim()}</pre>
            </RSection>
          );
        })}
      </div>
    </RSection>
  );
}

// ── Reader ────────────────────────────────────────────────────────────────────

interface ReaderSection {
  num: string;
  title: string;
  hint?: string;
  defaultOpen?: boolean;
  blocks?: Array<{ kind?: string; items?: string[]; html?: string }>;
}

interface ReaderData {
  sections?: ReaderSection[];
  reader_meta?: { model?: string; provider?: string };
  ocr_available?: boolean;
  ocr_meta?: { provider: string; model: string };
}

interface SumProgress {
  stage?: string;
  chunks_done?: number;
  chunks_total?: number;
  active?: boolean;
}

export function Reader({ paper }: { paper: Paper | null }) {
  const [data, setData] = useState<ReaderData | null>(null);
  const [loading, setLoading] = useState(false);
  const [summarising, setSummarising] = useState(false);
  const [sumProgress, setSumProgress] = useState<SumProgress | null>(null);
  const [ocrRunning, setOcrRunning] = useState(false);
  const [ocrProgress, setOcrProgress] = useState({ done: 0, total: 0 });
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const sumProgPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPoll = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  const stopSumProgPoll = () => { if (sumProgPollRef.current) { clearInterval(sumProgPollRef.current); sumProgPollRef.current = null; } };

  const startSummarisePoll = (paperId: string) => {
    setSumProgress(null);
    let elapsed = 0;
    sumProgPollRef.current = setInterval(() => {
      api.fetchSummariseProgress(paperId).then(raw => {
        const p = raw as SumProgress | null;
        if (p?.active) setSumProgress(p);
        else if (p && !p.active) { setSumProgress(null); stopSumProgPoll(); }
      }).catch(() => {});
    }, 3000);
    pollRef.current = setInterval(() => {
      elapsed += 5;
      api.fetchReader(paperId)
        .then(raw => {
          const d = raw as ReaderData | null;
          if (d?.reader_meta?.model) {
            setData(d);
            setSummarising(false);
            setSumProgress(null);
            stopPoll();
            stopSumProgPoll();
            window.dispatchEvent(new CustomEvent("rs:refreshPapers"));
            window.dispatchEvent(new CustomEvent("rs:journey-action", { detail: { paperId, action: "summarized" } }));
          }
        })
        .catch(() => {});
      if (elapsed >= 300) { setSummarising(false); stopPoll(); stopSumProgPoll(); }
    }, 5000);
  };

  useEffect(() => {
    if (!paper) return;
    setLoading(true);
    setData(null);
    setSummarising(false);
    stopPoll();
    api.fetchReader(paper.id)
      .then(raw => {
        const d = raw as ReaderData | null;
        setData(d); setLoading(false);
        if (d?.reader_meta?.model)
          window.dispatchEvent(new CustomEvent("rs:journey-action", { detail: { paperId: paper.id, action: "summarized" } }));
      })
      .catch(() => setLoading(false));

    const onOcrProgress = (ev: Event) => {
      const detail = (ev as CustomEvent).detail;
      if (detail?.paperId !== paper.id) return;
      setOcrRunning(true);
      setOcrProgress({ done: detail.pagesDone, total: detail.pagesTotal });
    };
    const onOcrComplete = (ev: Event) => {
      const detail = (ev as CustomEvent).detail;
      if (detail?.paperId !== paper.id) return;
      setOcrRunning(false);
      api.fetchReader(paper.id).then(raw => { const d = raw as ReaderData | null; if (d) setData(d); }).catch(() => {});
    };
    window.addEventListener("rs:ocr-progress", onOcrProgress);
    window.addEventListener("rs:ocr-complete", onOcrComplete);
    return () => {
      stopPoll();
      stopSumProgPoll();
      window.removeEventListener("rs:ocr-progress", onOcrProgress);
      window.removeEventListener("rs:ocr-complete", onOcrComplete);
    };
  }, [paper?.id]);

  if (!paper) return <div style={{ padding: 60, color: "var(--ink-4)" }}>Select a paper</div>;

  const sections = data?.sections || [];
  const meta = data?.reader_meta;
  const summarised = Boolean(meta?.model);
  const ocrAvailable = Boolean(data?.ocr_available);
  const ocrMeta = data?.ocr_meta;
  const authors = (paper.authors || []).join(", ");

  const PENDING_SECTIONS = [
    { num: "01", title: "Model summary",    hint: "~200 words",  what: "A plain-English summary of the core method, key idea, and results." },
    { num: "02", title: "Contributions",    hint: "3–6 items",   what: "Bullet list of the paper's stated contributions." },
    { num: "03", title: "Key claims",       hint: "3–5 items",   what: "Quantitative results and empirical claims worth verifying." },
    { num: "04", title: "What to question", hint: undefined,      what: "Limitations, eval gaps, and assumptions the LLM flags." },
    { num: "05", title: "Related lineage",  hint: undefined,      what: "Ancestor papers and direct competitors identified by the model." },
    { num: "06", title: "Follow-up",        hint: "suggestions",  what: "Papers and experiments worth reading next." },
  ];

  const triggerSummarise = () => {
    setSummarising(true);
    api.triggerSummarise(paper.id)
      .then(() => startSummarisePoll(paper.id))
      .catch(() => setSummarising(false));
  };

  return (
    <div className="reader">
      <div className="crumbs">
        <span>{paper.source}</span>
        <span className="sep">/</span>
        <span>{paper.venue}</span>
        <span className="sep">/</span>
        <span>{paper.id}</span>
        {summarised ? (
          <span className="live">
            <span className="pulse" />
            {meta!.provider || "local"} · {meta!.model}
          </span>
        ) : (
          <span className="live" style={{ color: "var(--ink-4)" }}>not summarised</span>
        )}
        {ocrMeta && (
          <span className="live" style={{ color: "var(--ink-3)", marginLeft: 6 }}>
            ⌖ OCR · {ocrMeta.provider} · {ocrMeta.model}
          </span>
        )}
      </div>

      <h1><MathText text={paper.title} /></h1>
      <div className="byline">
        <b>{authors}</b>
      </div>

      <div className="tldr"><MathText text={paper.abstract} /></div>

      {loading && <div style={{ padding: "20px 0", color: "var(--ink-4)", fontSize: 12 }}>Loading…</div>}

      {!loading && !summarised && (
        <div style={{ margin: "20px 0 4px", padding: "14px 18px", background: "var(--bg-2)", borderRadius: 6, border: "1px solid var(--rule)", display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{ flex: 1 }}>
            {summarising ? (
              <>
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>
                  {sumProgress?.stage === "map_reduce"
                    ? `Map-reduce · chunk ${sumProgress.chunks_done} / ${sumProgress.chunks_total}`
                    : "Summarising with LLM…"}
                </div>
                {sumProgress?.stage === "map_reduce" ? (
                  <div style={{ marginTop: 6 }}>
                    <div style={{ height: 4, background: "var(--bg-3)", borderRadius: 2, overflow: "hidden" }}>
                      <div style={{
                        height: "100%", borderRadius: 2, background: "var(--rust)",
                        width: `${((sumProgress.chunks_done ?? 0) / (sumProgress.chunks_total ?? 1)) * 100}%`,
                        transition: "width 0.4s ease",
                      }} />
                    </div>
                    <div style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 4 }}>
                      Condensing full text before final synthesis…
                    </div>
                  </div>
                ) : (
                  <div style={{ fontSize: 12, color: "var(--ink-3)", lineHeight: 1.5 }}>
                    This may take 30–120 s depending on your provider. Cancel if it seems stuck.
                  </div>
                )}
              </>
            ) : (
              <>
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>LLM summary not yet generated</div>
                <div style={{ fontSize: 12, color: "var(--ink-3)", lineHeight: 1.5 }}>
                  Once you run an LLM over this paper, the sections below will populate.
                  Configure your LLM provider in <b>⚙ Settings</b>.
                </div>
              </>
            )}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
            {summarising && (
              <>
                <div style={{ width: 14, height: 14, border: "2px solid var(--rust)", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
                <button className="ghost" style={{ whiteSpace: "nowrap" }} onClick={() => { setSummarising(false); stopPoll(); }}>
                  ✕ Cancel
                </button>
              </>
            )}
            <button className="ghost" style={{ whiteSpace: "nowrap" }} onClick={triggerSummarise} disabled={summarising}>
              {summarising ? "↻ Retry" : "↻ Summarise now"}
            </button>
          </div>
        </div>
      )}

      {summarised
        ? sections.map(sec => (
            <RSection key={sec.num} num={sec.num} title={sec.title} hint={sec.hint} defaultOpen={sec.defaultOpen}>
              {(sec.blocks || []).map((b, i) => {
                if (b.kind === "ul") return (
                  <ul key={i}>
                    {(b.items || []).map((item, j) => <li key={j}><Md text={item} inline /></li>)}
                  </ul>
                );
                return <Md key={i} text={b.html || ""} />;
              })}
            </RSection>
          ))
        : PENDING_SECTIONS.map(sec => (
            <div key={sec.num} style={{ opacity: 0.45, pointerEvents: "none" }}>
              <RSection num={sec.num} title={sec.title} hint={sec.hint}>
                <p style={{ fontStyle: "italic", color: "var(--ink-4)", fontSize: 12, padding: "4px 0" }}>
                  {sec.what}
                </p>
              </RSection>
            </div>
          ))
      }

      {ocrRunning && (
        <div style={{ margin: "12px 0 4px", padding: "14px 18px", background: "var(--bg-2)", borderRadius: 6, border: "1px solid var(--rule)", display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>OCR in progress…</div>
            <div style={{ fontSize: 12, color: "var(--ink-3)", lineHeight: 1.5 }}>
              {ocrProgress.total > 0
                ? `${ocrProgress.done} of ${ocrProgress.total} pages extracted.`
                : "Rendering PDF pages…"}
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
            <div style={{ width: 14, height: 14, border: "2px solid var(--rust)", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
            {ocrProgress.total > 0 && (
              <span style={{ fontFamily: "monospace", fontSize: 13, color: "var(--rust)", minWidth: 52, textAlign: "right" }}>
                {ocrProgress.done}/{ocrProgress.total}
              </span>
            )}
          </div>
        </div>
      )}

      {ocrAvailable && <OcrTextViewer paper={paper} />}
    </div>
  );
}
