import { useState, useRef, useEffect } from "react";
import type { Paper } from "../types";

const EXPLAINER_HTML = `<!doctype html>
<html><head><meta charset="utf-8"><style>
  :root {
    --bg: #FDFBF6; --card: #F2ECDF; --chip: #F2ECDF;
    --ink: #1F1A14; --ink-2: #3A332A; --ink-3: #6A6253; --ink-4: #9A9184;
    --rust: #B04428; --skip: #D8CDB3; --skip-op: 0.5;
    --btn-bg: #1F1A14; --btn-fg: #FDFBF6; --btn-border: rgba(31,26,20,0.15);
  }
  :root[data-theme="dark"] {
    --bg: #151310; --card: #1F1C18; --chip: #2A2621;
    --ink: #F0EBE0; --ink-2: #D4CEC0; --ink-3: #A8A293; --ink-4: #74705F;
    --rust: #D9532F; --skip: #3A342B; --skip-op: 0.7;
    --btn-bg: #F0EBE0; --btn-fg: #151310; --btn-border: rgba(240,235,224,0.15);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Inter Tight', -apple-system, sans-serif;
    background: var(--bg); color: var(--ink);
    padding: 28px 36px; line-height: 1.5;
  }
  h2 { font-size: 15px; font-weight: 500; color: var(--rust); margin-bottom: 6px; letter-spacing: -0.005em; }
  p { font-size: 13.5px; color: var(--ink-2); margin-bottom: 16px; }
  .stage {
    background: var(--card); border-radius: 8px; padding: 22px;
    margin-bottom: 18px; position: relative; overflow: hidden;
  }
  .layers { display: flex; flex-direction: column; gap: 8px; }
  .layer {
    display: flex; align-items: center; gap: 10px;
    height: 32px; padding: 0 12px;
    background: var(--bg); border-radius: 5px;
    font-size: 11px; color: var(--ink-2);
    position: relative; font-family: 'JetBrains Mono', monospace;
  }
  .layer .lbl { width: 60px; color: var(--ink-3); }
  .tokens { display: flex; gap: 3px; flex: 1; }
  .tok {
    width: 16px; height: 18px; border-radius: 2px;
    background: var(--rust); opacity: 1;
    transition: all 0.35s ease;
  }
  .tok.skip { background: var(--skip); opacity: var(--skip-op); }
  .meta { font-size: 10px; color: var(--ink-4); margin-left: auto; }
  .controls { display: flex; gap: 8px; align-items: center; margin-top: 16px; }
  .ctrl {
    padding: 8px 14px; background: var(--btn-bg); color: var(--btn-fg);
    border: 0; border-radius: 5px; font-size: 12px; cursor: pointer; font-family: inherit;
  }
  .ctrl.ghost { background: transparent; color: var(--ink-2); border: 1px solid var(--btn-border); }
  .slider-row { display: flex; align-items: center; gap: 10px; font-size: 11px; color: var(--ink-3); }
  input[type=range] { flex: 1; accent-color: var(--rust); }
  .stat { display: inline-flex; gap: 6px; padding: 4px 10px; background: var(--chip); border-radius: 999px; font-size: 11px; margin-right: 6px; font-family: 'JetBrains Mono', monospace; color: var(--ink-2); }
  .stat b { color: var(--rust); }
  .badge { display: inline-block; padding: 2px 8px; background: var(--rust); color: var(--bg); border-radius: 999px; font-size: 10px; margin-bottom: 10px; font-family: 'JetBrains Mono', monospace; letter-spacing: 0.05em; }
<\/style><\/head>
<body>
  <span class="badge">MODEL-GENERATED · INTERACTIVE<\/span>
  <h2>How Sparse Mixture-of-Depths routes tokens<\/h2>
  <p>Each layer's gate decides which tokens skip the block. Drag the slider to vary the skip-rate and watch the routing pattern settle. Highlighted cells = processed; faded = skipped (KV-only path).<\/p>

  <div class="stage">
    <div class="layers" id="layers"><\/div>
  <\/div>

  <div class="slider-row">
    <span>Skip-rate<\/span>
    <input type="range" id="rate" min="0" max="0.8" step="0.05" value="0.45" />
    <span id="rateLbl" style="font-family: 'JetBrains Mono', monospace; color: var(--rust); width: 48px;">0.45<\/span>
  <\/div>

  <div class="controls">
    <button class="ctrl" id="step">Step forward<\/button>
    <button class="ctrl ghost" id="reset">Reset<\/button>
    <div style="margin-left: auto;">
      <span class="stat">FLOPs <b id="flops">0.55×<\/b><\/span>
      <span class="stat">Acc Δ <b>+0.1%<\/b><\/span>
    <\/div>
  <\/div>

  <script>
    const L = 8, T = 24;
    const root = document.getElementById('layers');
    const rate = document.getElementById('rate');
    const rateLbl = document.getElementById('rateLbl');
    const flopsEl = document.getElementById('flops');

    function render(skip) {
      root.innerHTML = '';
      for (let l = 0; l < L; l++) {
        const row = document.createElement('div');
        row.className = 'layer';
        row.innerHTML = '<span class="lbl">L' + String(l).padStart(2, '0') + '<\\/span>';
        const toks = document.createElement('div');
        toks.className = 'tokens';
        let kept = 0;
        for (let t = 0; t < T; t++) {
          const d = document.createElement('div');
          d.className = 'tok';
          const h = Math.sin(l * 17.31 + t * 3.57) * 10000;
          const r = h - Math.floor(h);
          if (r < skip) d.classList.add('skip'); else kept++;
          toks.appendChild(d);
        }
        row.appendChild(toks);
        const m = document.createElement('span');
        m.className = 'meta';
        m.textContent = kept + '/' + T + ' processed';
        row.appendChild(m);
        root.appendChild(row);
      }
      flopsEl.textContent = (1 - skip).toFixed(2) + '×';
    }
    rate.addEventListener('input', () => {
      rateLbl.textContent = (+rate.value).toFixed(2);
      render(+rate.value);
    });
    document.getElementById('reset').addEventListener('click', () => {
      rate.value = 0.45; rateLbl.textContent = '0.45'; render(0.45);
    });
    document.getElementById('step').addEventListener('click', () => {
      const v = Math.min(0.8, +rate.value + 0.05);
      rate.value = v; rateLbl.textContent = v.toFixed(2); render(v);
    });
    render(0.45);
    window.addEventListener('message', (e) => {
      if (e.data && e.data.type === '__theme') {
        document.documentElement.setAttribute('data-theme', e.data.theme);
      }
    });
    try { parent.postMessage({ type: '__explainer_ready' }, '*'); } catch(_){}
  <\/script>
<\/body><\/html>`;

interface StreamLogEntry { t: string; msg: string; }

export interface ExplainerPanelProps {
  paper: Paper | null;
}

export function ExplainerPanel({ paper }: ExplainerPanelProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [generating, setGen] = useState(false);
  const [ready, setReady] = useState(true);
  const [streamLog] = useState<StreamLogEntry[]>([
    { t: "00:00.21", msg: "Parsing figures 3, 4, 7 from PDF…" },
    { t: "00:01.18", msg: "Identifying core mechanism: saliency-gated routing." },
    { t: "00:02.41", msg: "Drafting interactive metaphor: tokens as cells per layer." },
    { t: "00:03.90", msg: "Generating HTML + JS (1,840 tokens)…" },
    { t: "00:05.02", msg: "Validated. Rendered in sandbox." },
  ]);

  useEffect(() => {
    const sync = () => {
      const theme = document.documentElement.getAttribute("data-theme") || "light";
      iframeRef.current?.contentWindow?.postMessage({ type: "__theme", theme }, "*");
    };
    const onMsg = (e: MessageEvent<{ type?: string }>) => {
      if (e.data?.type === "__explainer_ready") sync();
    };
    window.addEventListener("message", onMsg);
    const mo = new MutationObserver(sync);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    sync();
    return () => { window.removeEventListener("message", onMsg); mo.disconnect(); };
  }, [paper?.id]);

  const regenerate = () => {
    setGen(true); setReady(false);
    setTimeout(() => { setGen(false); setReady(true); }, 1500);
  };

  void ready;

  return (
    <div className="explainer">
      <div className="ex-head">
        <div>
          <div className="ex-kicker">INTERACTIVE EXPLAINER</div>
          <div className="ex-title">Generated visualization · {paper?.title?.slice(0, 60)}{(paper?.title?.length ?? 0) > 60 ? "…" : ""}</div>
        </div>
        <div className="ex-actions">
          <button className="ghost" onClick={regenerate}>↻ Regenerate</button>
          <button className="ghost">↗ Open standalone</button>
          <button className="ghost">⇣ Download HTML</button>
        </div>
      </div>

      <div className="ex-sources">
        <span className="ex-src-label">Source</span>
        <div className="ex-src-chips">
          <button className="ex-chip on">PDF</button>
          <button className="ex-chip">Abstract</button>
          <button className="ex-chip">Supplement</button>
          <button className="ex-chip">Paste URL…</button>
          <button className="ex-chip">Upload file</button>
        </div>
      </div>

      <div className="ex-stage">
        {generating ? (
          <div className="ex-generating">
            <div className="ex-log">
              {streamLog.map((l, i) => (
                <div key={i} className="ex-log-row">
                  <span className="t">{l.t}</span>
                  <span>{l.msg}</span>
                </div>
              ))}
              <div className="ex-log-row pending">
                <span className="t">··:··.··</span>
                <span>Regenerating<span className="dots"><i>.</i><i>.</i><i>.</i></span></span>
              </div>
            </div>
          </div>
        ) : (
          <iframe
            ref={iframeRef}
            key={paper?.id}
            className="ex-frame"
            title="explainer"
            sandbox="allow-scripts allow-same-origin"
            srcDoc={EXPLAINER_HTML}
          />
        )}
      </div>

      <div className="ex-foot">
        <span>⚙ Model: local llama-3.1-70B · 1,840 output tokens · 4.9s</span>
        <span style={{ marginLeft: "auto" }}>Sandboxed HTML · no network access</span>
      </div>
    </div>
  );
}
