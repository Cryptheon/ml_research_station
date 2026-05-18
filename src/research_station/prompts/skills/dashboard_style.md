---
name: dashboard_style
description: Meridian design system — injected when the agent creates HTML dashboards or visualisations.
triggers: dashboard, html, visuali, chart, plot, d3, interactive, render, svg, figure, table, report
---

## Meridian design system — apply to all HTML dashboards

Include this CSS block verbatim inside every `<style>` tag. Do not substitute with Bootstrap, Tailwind, or any other framework.

```css
:root {
  --bg:      #FDFCF8;
  --bg-1:    #F4EFE3;
  --bg-2:    #E9E0CA;
  --bg-3:    #DDD0B3;
  --ink:     #241C12;
  --ink-2:   #3E3425;
  --ink-3:   #6B5D48;
  --ink-4:   #9A8C73;
  --ink-5:   #BFB298;
  --rule:    rgba(36,28,18,0.08);
  --rule-2:  rgba(36,28,18,0.14);
  --rust:    #A23E22;
  --rust-2:  #8B3319;
  --ember:   #C75F30;
  --sulfur:  #B0853A;
  --clay:    #7A4E33;
  --ok:      #5C7A3A;
  --font:    "Inter Tight", "Inter", Helvetica, sans-serif;
  --mono:    "JetBrains Mono", ui-monospace, Menlo, monospace;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg); color: var(--ink);
  font-family: var(--font); font-size: 13px; line-height: 1.6;
  padding: 28px 32px; max-width: 960px; margin: 0 auto;
}
h1 { font-size: 20px; font-weight: 700; color: var(--ink); margin-bottom: 4px; letter-spacing: -0.3px; }
h2 { font-size: 15px; font-weight: 600; color: var(--ink-2); margin: 24px 0 10px; }
h3 { font-size: 11px; font-weight: 600; color: var(--ink-4);
     text-transform: uppercase; letter-spacing: 0.7px; margin: 18px 0 8px; }
p  { color: var(--ink-2); margin-bottom: 10px; }
a  { color: var(--rust); text-decoration: none; }
a:hover { color: var(--ember); text-decoration: underline; }
code { font-family: var(--mono); font-size: 11px; background: var(--bg-2);
       border: 1px solid var(--rule-2); border-radius: 3px; padding: 1px 5px; color: var(--clay); }
pre  { font-family: var(--mono); font-size: 11px; background: var(--bg-1);
       border: 1px solid var(--rule-2); border-radius: 6px;
       padding: 12px 16px; overflow-x: auto; color: var(--ink-2); }
hr   { border: none; border-top: 1px solid var(--rule-2); margin: 20px 0; }

/* ── Cards ── */
.card {
  background: var(--bg-1); border: 1px solid var(--rule-2);
  border-radius: 8px; padding: 18px 22px; margin-bottom: 16px;
}
.card-title {
  font-size: 11px; font-weight: 600; color: var(--rust);
  text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 10px;
}
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }

/* ── Stat / metric blocks ── */
.stat { display: inline-flex; flex-direction: column; gap: 3px; }
.stat-val { font-size: 26px; font-weight: 700; color: var(--rust); line-height: 1; letter-spacing: -0.5px; }
.stat-label { font-size: 10px; color: var(--ink-4); text-transform: uppercase; letter-spacing: 0.5px; }
.stat-sub   { font-size: 11px; color: var(--ink-3); }

/* ── Tags / badges ── */
.tag {
  display: inline-block; font-size: 10px; font-weight: 500;
  padding: 2px 8px; border-radius: 20px;
  border: 1px solid var(--rule-2); color: var(--ink-3); background: var(--bg-2); margin: 2px;
}
.tag.accent {
  background: color-mix(in srgb, var(--rust) 10%, transparent);
  border-color: color-mix(in srgb, var(--rust) 28%, transparent);
  color: var(--rust);
}
.tag.ok {
  background: color-mix(in srgb, var(--ok) 10%, transparent);
  border-color: color-mix(in srgb, var(--ok) 28%, transparent);
  color: var(--ok);
}

/* ── Tabs ── */
.tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--rule-2); margin-bottom: 18px; }
.tab-btn {
  padding: 7px 16px; font-size: 12px; font-weight: 500; cursor: pointer;
  border: 1px solid transparent; border-bottom: none; background: none;
  color: var(--ink-4); border-radius: 5px 5px 0 0; font-family: var(--font);
}
.tab-btn:hover  { background: var(--bg-2); color: var(--ink-2); }
.tab-btn.active {
  background: var(--bg-1); color: var(--rust);
  border-color: var(--rule-2); border-bottom-color: var(--bg-1);
  margin-bottom: -1px;
}
.tab-panel { display: none; }
.tab-panel.active { display: block; }

/* ── Tables ── */
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th {
  text-align: left; padding: 7px 12px;
  font-size: 10px; font-weight: 600; color: var(--ink-4);
  text-transform: uppercase; letter-spacing: 0.5px;
  border-bottom: 1px solid var(--rule-2); background: var(--bg-1);
}
td { padding: 8px 12px; border-bottom: 1px solid var(--rule); color: var(--ink-2); }
tbody tr:hover td { background: var(--bg-2); }
tbody tr:last-child td { border-bottom: none; }

/* ── Buttons ── */
.btn {
  padding: 7px 16px; border-radius: 5px; font-size: 12px;
  cursor: pointer; font-family: var(--font);
  border: 1px solid var(--rule-2); background: var(--bg-1); color: var(--ink-2);
  transition: background 0.12s, border-color 0.12s;
}
.btn:hover { background: var(--bg-2); border-color: var(--rule-2); }
.btn.primary { background: var(--rust); color: #FBF5E8; border-color: var(--rust); }
.btn.primary:hover { background: var(--rust-2); border-color: var(--rust-2); }

/* ── Section header with accent rule ── */
.section-head {
  display: flex; align-items: center; gap: 10px; margin: 24px 0 12px;
}
.section-head::after {
  content: ""; flex: 1; height: 1px; background: var(--rule-2);
}

/* ── Progress / bar ── */
.bar-track { background: var(--bg-3); border-radius: 3px; height: 6px; overflow: hidden; }
.bar-fill  { background: var(--rust); height: 100%; border-radius: 3px; transition: width 0.3s; }

/* ── Tooltip (for D3 / chart hovers) ── */
.tooltip {
  position: absolute; pointer-events: none; padding: 7px 11px;
  background: var(--ink); color: var(--bg); border-radius: 5px;
  font-size: 11px; line-height: 1.4; white-space: nowrap;
  box-shadow: 0 2px 8px rgba(36,28,18,0.18);
}
```

### Chart / visualisation colour palette

Use these in order for data series:

| Purpose      | Value     | Token       |
|-------------|-----------|-------------|
| Primary     | #A23E22   | --rust      |
| Secondary   | #C75F30   | --ember     |
| Tertiary    | #B0853A   | --sulfur    |
| Quaternary  | #5C7A3A   | --ok        |
| Quinary     | #6B5D48   | --ink-3     |

For SVG/Canvas: reference these as hex strings. For CSS-controlled charts use `var(--rust)` etc.

### Design rules

- Background is warm parchment (#FDFCF8) — never pure white or dark mode.
- Body max-width: 960px, centred, padding 28px 32px.
- Use `.card` for all content blocks — never raw `<div>` with inline borders.
- Use `h3` (small-caps, --ink-4) for section labels, not bold paragraph text.
- Interactive tab switching: toggle `.active` class on `.tab-btn` and `.tab-panel` via JS.
- Tooltips for all chart hover states — use the `.tooltip` class above.
- All chart axes: font `var(--font)`, size 10–11px, colour `var(--ink-4)`.
