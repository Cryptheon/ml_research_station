import { useState, useEffect } from "react";
import { api } from "../api";

interface TokenPeriod {
  prompt_tokens?: number;
  completion_tokens?: number;
  requests?: number;
}

interface ModelUsageRow {
  model: string;
  endpoint: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  avg_latency_s: number;
}

interface TokenUsage {
  total?: TokenPeriod;
  today?: TokenPeriod;
  week?: TokenPeriod;
  by_model?: ModelUsageRow[];
}

interface PaperViewItem {
  paper_id: string;
  title?: string;
  views?: number;
}

interface PaperViews {
  top_user?: PaperViewItem[];
  top_agent?: PaperViewItem[];
}

function fmt(n: number | undefined | null): string {
  if (!n) return "0";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

interface StatCardProps {
  label: string;
  tokens: number;
  prompt: number;
  completion: number;
  requests: number;
}

function StatCard({ label, tokens, prompt, completion, requests }: StatCardProps) {
  return (
    <div style={{
      background: "var(--bg-1)", border: "1px solid var(--rule-2)",
      borderRadius: 6, padding: "14px 18px", flex: 1,
    }}>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--rust)", marginBottom: 8 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 700, letterSpacing: "-0.02em", color: "var(--ink-1)", lineHeight: 1 }}>{fmt(tokens)}</div>
      <div style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 4 }}>tokens total</div>
      <div style={{ marginTop: 10, display: "flex", gap: 12, fontSize: 11, color: "var(--ink-3)" }}>
        <span><span style={{ color: "var(--ink-4)" }}>↑</span> {fmt(prompt)} in</span>
        <span><span style={{ color: "var(--ink-4)" }}>↓</span> {fmt(completion)} out</span>
        <span>{fmt(requests)} req</span>
      </div>
    </div>
  );
}

interface PaperListProps {
  items: PaperViewItem[] | undefined;
  onOpenPaper: (id: string) => void;
  emptyMsg: string;
}

function PaperList({ items, onOpenPaper, emptyMsg }: PaperListProps) {
  if (!items || items.length === 0) {
    return <div style={{ color: "var(--ink-4)", fontSize: 12, padding: "10px 0" }}>{emptyMsg}</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {items.map((item, i) => (
        <div key={i}
          style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 8px", borderRadius: 4, cursor: "pointer", fontSize: 12 }}
          className="usage-paper-row"
          onClick={() => onOpenPaper(item.paper_id)}
        >
          <span style={{ color: "var(--ink-4)", fontFamily: "var(--font-mono)", fontSize: 10, flexShrink: 0, minWidth: 20, textAlign: "right" }}>
            {item.views != null ? item.views : ""}
          </span>
          <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--ink-2)" }}>
            {item.title || item.paper_id}
          </span>
          <span style={{ color: "var(--ink-5)", fontFamily: "var(--font-mono)", fontSize: 10, flexShrink: 0 }}>
            {item.paper_id.split(":")[0]}
          </span>
        </div>
      ))}
    </div>
  );
}

export interface UsageModalProps {
  open: boolean;
  onClose: () => void;
  onOpenPaper: (id: string) => void;
}

export function UsageModal({ open, onClose, onOpenPaper }: UsageModalProps) {
  const [tab, setTab] = useState("tokens");
  const [usage, setUsage] = useState<TokenUsage | null>(null);
  const [views, setViews] = useState<PaperViews | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    Promise.all([
      api.fetchTokenUsage(),
      api.fetchPaperViews(),
    ]).then(([u, v]) => {
      setUsage(u as TokenUsage);
      setViews(v as PaperViews);
    }).finally(() => setLoading(false));
  }, [open]);

  if (!open) return null;

  const total = usage?.total ?? {};
  const today = usage?.today ?? {};
  const week = usage?.week ?? {};
  const byModel = usage?.by_model ?? [];

  return (
    <div className="modal-scrim" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="ingest-modal" style={{ width: 680 }}>
        <div className="im-head">
          <div>
            <div className="im-kicker">Meridian · Activity</div>
            <div className="im-title">Usage</div>
          </div>
          <button className="im-close btn" onClick={onClose}>✕</button>
        </div>

        <div style={{ display: "flex", gap: 0, borderBottom: "1px solid var(--rule-2)", padding: "0 24px" }}>
          {([ ["tokens", "Tokens"], ["views", "Papers"] ] as [string, string][]).map(([k, lbl]) => (
            <button key={k} onClick={() => setTab(k)} style={{
              padding: "10px 16px", fontSize: 12, fontWeight: tab === k ? 600 : 400,
              color: tab === k ? "var(--ink-1)" : "var(--ink-4)",
              borderBottom: tab === k ? "2px solid var(--rust)" : "2px solid transparent",
              background: "none", border: "none",
              cursor: "pointer", marginBottom: -1,
            }}>{lbl}</button>
          ))}
        </div>

        <div style={{ padding: "20px 24px", minHeight: 200 }}>
          {loading && <div style={{ color: "var(--ink-4)", fontSize: 13 }}>Loading…</div>}

          {!loading && tab === "tokens" && (
            <>
              <div style={{ display: "flex", gap: 10, marginBottom: 20 }}>
                <StatCard label="Today"
                  tokens={(today.prompt_tokens ?? 0) + (today.completion_tokens ?? 0)}
                  prompt={today.prompt_tokens ?? 0} completion={today.completion_tokens ?? 0}
                  requests={today.requests ?? 0} />
                <StatCard label="This Week"
                  tokens={(week.prompt_tokens ?? 0) + (week.completion_tokens ?? 0)}
                  prompt={week.prompt_tokens ?? 0} completion={week.completion_tokens ?? 0}
                  requests={week.requests ?? 0} />
                <StatCard label="All Time"
                  tokens={(total.prompt_tokens ?? 0) + (total.completion_tokens ?? 0)}
                  prompt={total.prompt_tokens ?? 0} completion={total.completion_tokens ?? 0}
                  requests={total.requests ?? 0} />
              </div>

              {byModel.length === 0 ? (
                <div style={{ color: "var(--ink-4)", fontSize: 12 }}>
                  No token usage recorded yet. The server records usage for every LLM call made while it's running.
                </div>
              ) : (
                <div style={{ background: "var(--bg-1)", border: "1px solid var(--rule-2)", borderRadius: 6, overflow: "hidden" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                    <thead>
                      <tr style={{ background: "var(--bg-2)" }}>
                        {["Model", "Endpoint", "Requests", "Prompt", "Completion", "Avg latency"].map(h => (
                          <th key={h} style={{ padding: "7px 10px", textAlign: "left", color: "var(--ink-3)", fontWeight: 600, fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em", borderBottom: "1px solid var(--rule-2)" }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {byModel.map((r, i) => (
                        <tr key={i} style={{ borderBottom: "1px solid var(--rule-2)" }}>
                          <td style={{ padding: "7px 10px", color: "var(--ink-1)", fontWeight: 500 }}>{r.model}</td>
                          <td style={{ padding: "7px 10px", color: "var(--ink-3)" }}>{r.endpoint}</td>
                          <td style={{ padding: "7px 10px", color: "var(--ink-2)", fontFamily: "var(--font-mono)" }}>{r.requests}</td>
                          <td style={{ padding: "7px 10px", color: "var(--ink-2)", fontFamily: "var(--font-mono)" }}>{fmt(r.prompt_tokens)}</td>
                          <td style={{ padding: "7px 10px", color: "var(--ink-2)", fontFamily: "var(--font-mono)" }}>{fmt(r.completion_tokens)}</td>
                          <td style={{ padding: "7px 10px", color: "var(--ink-4)", fontFamily: "var(--font-mono)" }}>{r.avg_latency_s}s</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}

          {!loading && tab === "views" && (
            <div style={{ display: "flex", gap: 20 }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--rust)", marginBottom: 10 }}>
                  You opened most
                </div>
                <PaperList items={views?.top_user} onOpenPaper={id => { onOpenPaper(id); onClose(); }} emptyMsg="No papers opened by you yet." />
              </div>
              <div style={{ width: 1, background: "var(--rule-2)" }} />
              <div style={{ flex: 1 }}>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--rust)", marginBottom: 10 }}>
                  Agent accessed most
                </div>
                <PaperList items={views?.top_agent} onOpenPaper={id => { onOpenPaper(id); onClose(); }} emptyMsg="No papers opened by the agent yet." />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
