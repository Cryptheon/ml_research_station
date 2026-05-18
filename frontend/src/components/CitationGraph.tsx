/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState, useEffect, useRef, useCallback } from "react";
import * as _d3 from "d3";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const d3 = _d3 as any;
import { api } from "../api";
import type { Traversal } from "../types";

declare global {
  interface Window {
    refreshGraph?: () => void;
  }
}

// ── Colour palettes ───────────────────────────────────────────────────────────

const TOPIC_COLORS: Record<string, string> = {
  "LLM": "#a23e22", "Efficiency": "#a23e22", "Architecture": "#a23e22", "Training": "#a23e22",
  "Interpretability": "#c8712a",
  "RL": "#b89020", "World Models": "#b89020",
  "Geometric": "#3a8a6a", "Foundations": "#3a8a6a", "Representation": "#3a8a6a",
  "Bio": "#3a6ab5", "Imaging": "#3a6ab5", "Foundation": "#3a6ab5",
  "Generative": "#7a4ab5", "Audio": "#7a4ab5",
};

const TOPIC_LABEL_COLORS = [
  { color: "#a23e22", label: "LLM / Architecture" },
  { color: "#c8712a", label: "Interpretability" },
  { color: "#b89020", label: "RL / World Models" },
  { color: "#3a8a6a", label: "Geometric / Foundations" },
  { color: "#3a6ab5", label: "Bio / Imaging" },
  { color: "#7a4ab5", label: "Generative" },
  { color: "#8a7f74", label: "Other" },
];

export function nodeColor(topics: string[]): string {
  for (const t of (topics || [])) if (TOPIC_COLORS[t]) return TOPIC_COLORS[t];
  return "#8a7f74";
}

function nodeRadius(citedBy: number): number {
  return Math.max(5, 5 + Math.log1p(citedBy || 0) * 2.4);
}

const LLM_EDGE_COLORS: Record<string, string> = {
  extends: "#5a8af0", supersedes: "#b06af0", challenges: "#e05a5a",
  uses: "#3ab8c8", applies: "#3a8a6a", surveys: "#e0a040",
  baseline: "#8a7f74", concurrent: "#c8aa32",
};

const TRAVERSAL_DEPTH_COLORS = ["#f0b840", "#e07020", "#c04040", "#8040c0", "#3070b0"];

// ── Module-level caches (persist across tab switches) ─────────────────────────

const _graphCache: Record<string, any> = {};
const _graphState = { layoutMode: "force", showAuthor: false, showLlm: false, simMin: 0.4, simMax: 1 };

// ── DualRange ─────────────────────────────────────────────────────────────────

interface DualRangeProps {
  min: number; max: number; step: number;
  valueMin: number; valueMax: number;
  onChange: (lo: number, hi: number) => void;
}

function DualRange({ min, max, step, valueMin, valueMax, onChange }: DualRangeProps) {
  const pct = (v: number) => ((v - min) / (max - min)) * 100;
  const loPct = pct(valueMin), hiPct = pct(valueMax);
  const lowerOnTop = valueMin >= valueMax - step;
  return (
    <div className="dual-range-wrap">
      <div className="dual-range-track" style={{
        background: `linear-gradient(to right, var(--bg-3) ${loPct}%, var(--rust) ${loPct}%, var(--rust) ${hiPct}%, var(--bg-3) ${hiPct}%)`,
      }} />
      <input type="range" min={min} max={max} step={step} value={valueMin}
        onChange={e => onChange(Math.min(parseFloat(e.target.value), valueMax), valueMax)}
        className="dual-range-input" style={{ zIndex: lowerOnTop ? 3 : 2 }} />
      <input type="range" min={min} max={max} step={step} value={valueMax}
        onChange={e => onChange(valueMin, Math.max(parseFloat(e.target.value), valueMin))}
        className="dual-range-input" style={{ zIndex: lowerOnTop ? 2 : 3 }} />
    </div>
  );
}

// ── drawSemNeighbors ──────────────────────────────────────────────────────────

function drawSemNeighbors(
  grp: any, nodePositions: Record<string, { x: number; y: number }>,
  anchorId: string, neighbors: any[], simMin: number, simMax: number,
) {
  if (!anchorId || !neighbors?.length) return;
  const src = nodePositions[anchorId];
  if (!src) return;
  neighbors.forEach(nb => {
    if (nb.similarity < simMin || nb.similarity > simMax) return;
    const dst = nodePositions[nb.id];
    if (!dst) return;
    grp.append("line")
      .attr("x1", src.x).attr("y1", src.y).attr("x2", dst.x).attr("y2", dst.y)
      .attr("stroke", "#3a8a6a").attr("stroke-width", 1.8)
      .attr("stroke-opacity", 0.85).attr("stroke-dasharray", "5 3");
    const mx = (src.x + dst.x) / 2, my = (src.y + dst.y) / 2;
    grp.append("text")
      .attr("x", mx).attr("y", my - 3).attr("text-anchor", "middle")
      .attr("font-size", 9).attr("fill", "#3a8a6a")
      .attr("paint-order", "stroke").attr("stroke", "var(--bg)").attr("stroke-width", 2.5)
      .attr("pointer-events", "none").text(nb.similarity.toFixed(3));
  });
}

// ── CitationGraph ─────────────────────────────────────────────────────────────

interface CitationGraphProps {
  activeId: string | null;
  onSelect: (id: string) => void;
  active: boolean;
  traversal?: Traversal | null;
}

export function CitationGraph({ activeId, onSelect, active, traversal }: CitationGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef<{ zoom: any; svg: any } | null>(null);
  const nodePositionsRef = useRef<Record<string, { x: number; y: number }>>({});
  const semNeighborGroupRef = useRef<any>(null);
  const activeNeighborsRef = useRef<any[]>([]);
  const authorEdgeIndexRef = useRef<Record<string, any[]>>({});
  const activeIdRef = useRef(activeId);
  const [graphData, setGraphData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [tooltip, setTooltip] = useState<any>(null);
  const [stats, setStats] = useState({ nodes: 0, edges: 0, author: 0, llm: 0 });
  const [showAuthor] = useState(_graphState.showAuthor);
  const [showLlm, setShowLlm] = useState(_graphState.showLlm);
  const [layoutMode, setLayoutMode] = useState(_graphState.layoutMode);
  const [simMin, setSimMin] = useState(_graphState.simMin ?? -1);
  const [simMax, setSimMax] = useState(_graphState.simMax ?? 1);
  const simRangeRef = useRef({ min: _graphState.simMin ?? -1, max: _graphState.simMax ?? 1 });
  const [embedStatus, setEmbedStatus] = useState({ embedded: 0, total: 0 });
  const embedStatusRef = useRef({ embedded: 0, total: 0 });
  const [embedding, setEmbedding] = useState(false);
  const [classifying, setClassifying] = useState(false);
  const [classifyStatus, setClassifyStatus] = useState<any>(null);
  const [classifyNeighbors, setClassifyNeighbors] = useState(3);
  const [classifyAll, setClassifyAll] = useState(false);
  const [showEdgeInspector, setShowEdgeInspector] = useState(false);
  const [edgeRows, setEdgeRows] = useState<any[]>([]);
  const [edgeFilter, setEdgeFilter] = useState("all");
  const [edgeSearch, setEdgeSearch] = useState("");
  const [highlightMode, setHighlightMode] = useState<string | null>(null);
  const [manualIds, setManualIds] = useState(new Set<string>());
  const highlightModeRef = useRef<string | null>(null);
  const manualIdsRef = useRef(new Set<string>());
  const embedPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const classifyPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const llmLinkSelRef = useRef<any>(null);
  const llmLinksDataRef = useRef<any[]>([]);
  const simSettledRef = useRef(true);
  const nodeByIdRef = useRef<Record<string, any>>({});
  const [graphSelectedId, setGraphSelectedId] = useState<string | null>(null);
  const [graphNodeNeighbors, setGraphNodeNeighbors] = useState<any[] | null>(null);

  const _cacheKey = (lay: string, ath: boolean, llm: boolean) => `${lay}_${ath}_${llm}`;

  const fetchGraph = useCallback((layout?: string, author?: boolean, llm?: boolean, force = false) => {
    const lay = layout ?? "force";
    const ath = author ?? false;
    const llmE = llm ?? false;
    const key = _cacheKey(lay, ath, llmE);
    if (!force && _graphCache[key]) { setGraphData(_graphCache[key]); return; }
    setLoading(true);
    api.fetchGraphData(2000, false, 0.55, lay, ath, llmE).then((data: any) => {
      _graphCache[key] = data; setGraphData(data); setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const stopEmbedPoll = () => { if (embedPollRef.current) { clearInterval(embedPollRef.current); embedPollRef.current = null; } };
  const startEmbedPoll = () => {
    stopEmbedPoll();
    embedPollRef.current = setInterval(() => {
      api.fetchEmbedStatus().then((s: any) => {
        embedStatusRef.current = s; setEmbedStatus(s);
        if (s.embedded >= s.total && s.total > 0) { setEmbedding(false); stopEmbedPoll(); }
      });
    }, 2000);
  };

  const stopClassifyPoll = () => { if (classifyPollRef.current) { clearInterval(classifyPollRef.current); classifyPollRef.current = null; } };
  const startClassifyPoll = () => {
    stopClassifyPoll();
    classifyPollRef.current = setInterval(() => {
      api.fetchClassifyStatus().then((s: any) => {
        if (!s) return;
        setClassifyStatus(s);
        if (!s.running) {
          setClassifying(false); stopClassifyPoll();
          if (showLlm) { delete _graphCache[_cacheKey(layoutMode, showAuthor, true)]; fetchGraph(layoutMode, showAuthor, true); }
        }
      });
    }, 2000);
  };

  useEffect(() => {
    window.refreshGraph = () => {
      Object.keys(_graphCache).forEach(k => delete _graphCache[k]);
      fetchGraph(_graphState.layoutMode, _graphState.showAuthor, _graphState.showLlm, true);
    };
    return () => { delete window.refreshGraph; };
  }, [fetchGraph]);

  useEffect(() => {
    api.fetchEmbedStatus().then((s: any) => { embedStatusRef.current = s; setEmbedStatus(s); }).catch(() => {});
    api.fetchClassifyStatus().then((s: any) => { if (s) setClassifyStatus(s); }).catch(() => {});
    return () => { stopEmbedPoll(); stopClassifyPoll(); };
  }, []);

  useEffect(() => {
    if (!graphSelectedId) { setGraphNodeNeighbors(null); return; }
    setGraphNodeNeighbors(null);
    api.fetchNeighbors(graphSelectedId, Math.max(classifyNeighbors, 10))
      .then((data: any) => setGraphNodeNeighbors(data || []))
      .catch(() => setGraphNodeNeighbors([]));
  }, [graphSelectedId, classifyNeighbors]);

  useEffect(() => {
    highlightModeRef.current = highlightMode;
    if (highlightMode !== "manual") {
      manualIdsRef.current = new Set(); setManualIds(new Set()); return;
    }
    api.fetchManualPaperIds().then((ids: string[]) => {
      const s = new Set(ids); manualIdsRef.current = s; setManualIds(s);
    }).catch(() => {});
  }, [highlightMode]);

  const _HIGHLIGHT_RING: Record<string, string> = { manual: "var(--sulfur)", paper: "var(--rust)", wiki: "#4db6ac", web: "#7b9fd4" };

  function _applyHighlight(aid: string | null) {
    if (!containerRef.current || typeof d3 === "undefined") return;
    const mode = highlightModeRef.current;
    const mIds = manualIdsRef.current;
    const root = d3.select(containerRef.current);
    const nodeSel = root.selectAll(".graph-node");
    const linkSel = root.selectAll(".graph-edge");
    const _isHl = (id: string) => {
      if (!mode) return false;
      if (mode === "manual") return mIds.has(id);
      if (mode === "wiki") return id.startsWith("wikipedia:");
      if (mode === "web") return id.startsWith("web:");
      if (mode === "paper") return !id.startsWith("wikipedia:") && !id.startsWith("web:");
      return false;
    };
    const ringColor = _HIGHLIGHT_RING[mode!] || "rgba(247,242,232,0.7)";
    const anyHl = mode && (mode !== "manual" || mIds.size > 0);
    if (anyHl) {
      nodeSel.select("circle.nc").attr("opacity", (d: any) => (_isHl(d.id) || d.id === aid) ? 1 : 0.12);
      nodeSel.select("circle.ring")
        .attr("stroke", (d: any) => _isHl(d.id) ? ringColor : "rgba(247,242,232,0.3)")
        .attr("stroke-width", (d: any) => _isHl(d.id) ? 2.5 : 0)
        .attr("r", (d: any) => _isHl(d.id) ? d.r + 9 : d.r + 7);
      if (aid) {
        linkSel.attr("stroke", (l: any) => {
          if (!l.source || !l.target) return "rgba(36,28,18,0.14)";
          const sid = l.source.id || l.source, tid = l.target.id || l.target;
          if (sid === aid) return "#c8712a";
          if (tid === aid) return "#b89020";
          return "rgba(36,28,18,0.10)";
        }).attr("stroke-opacity", (l: any) => {
          const sid = l.source?.id ?? l.source, tid = l.target?.id ?? l.target;
          if (sid === aid || tid === aid) return 0.7;
          return (_isHl(sid) || _isHl(tid)) ? 0.45 : 0.04;
        });
      } else {
        linkSel.attr("stroke-opacity", (l: any) => {
          const sid = l.source?.id ?? l.source, tid = l.target?.id ?? l.target;
          return (_isHl(sid) || _isHl(tid)) ? 0.55 : 0.04;
        });
      }
      if (llmLinkSelRef.current) {
        llmLinkSelRef.current.attr("stroke-opacity", (l: any) => {
          const sid = typeof l.source === "object" ? l.source.id : l.source;
          const tid = typeof l.target === "object" ? l.target.id : l.target;
          if (aid && (sid === aid || tid === aid)) return 0.9;
          return 0.04;
        });
      }
    } else {
      nodeSel.select("circle.nc").attr("opacity", (d: any) => !aid || d.id === aid ? 1 : 0.5);
      nodeSel.select("circle.ring").attr("stroke", "rgba(247,242,232,0.7)").attr("stroke-width", 1.2).attr("r", (d: any) => d.r + 7);
      linkSel.attr("stroke-opacity", null);
      if (llmLinkSelRef.current) {
        llmLinkSelRef.current.attr("stroke-opacity", (l: any) => {
          if (!aid) return 0.55;
          const sid = typeof l.source === "object" ? l.source.id : l.source;
          const tid = typeof l.target === "object" ? l.target.id : l.target;
          return (sid === aid || tid === aid) ? 0.9 : 0.08;
        });
      }
    }
  }

  const handleEmbedAll = () => {
    setEmbedding(true);
    api.embedBatch().then(() => startEmbedPoll()).catch(() => setEmbedding(false));
  };

  const handleClassify = () => {
    setClassifying(true);
    api.classifyEdges(classifyNeighbors, classifyAll).then(() => startClassifyPoll()).catch(() => setClassifying(false));
  };

  const handleStopClassify = () => { api.stopClassifyEdges().catch(() => {}); };

  const handleOpenEdgeInspector = () => {
    api.fetchEdges("llm").then((rows: any[]) => { setEdgeRows(rows); setShowEdgeInspector(true); });
  };

  const handleLlmToggle = () => {
    const next = !showLlm; setShowLlm(next); _graphState.showLlm = next; fetchGraph(layoutMode, showAuthor, next);
  };
  const handleLayoutChange = (mode: string) => {
    setLayoutMode(mode); _graphState.layoutMode = mode; fetchGraph(mode, showAuthor, showLlm);
  };

  useEffect(() => {
    if (!active) return;
    fetchGraph(layoutMode, showAuthor, showLlm);
  }, [active]);

  useEffect(() => {
    if (!llmLinkSelRef.current || !llmLinksDataRef.current.length) return;
    const sel = llmLinkSelRef.current, data = llmLinksDataRef.current;
    const countByNode: Record<string, number> = {};
    const visible = new Set();
    const sorted = [...data].sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0));
    for (const e of sorted) {
      const src = typeof e.source === "object" ? e.source.id : e.source;
      countByNode[src] = (countByNode[src] || 0) + 1;
      if (countByNode[src] <= classifyNeighbors) visible.add(e);
    }
    sel.attr("display", (d: any) => visible.has(d) ? null : "none");
  }, [classifyNeighbors]);

  useEffect(() => { _applyHighlight(activeId); }, [highlightMode, manualIds, graphData, activeId]);

  const _traversalTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => {
    _traversalTimersRef.current.forEach(t => clearTimeout(t));
    _traversalTimersRef.current = [];
    if (!containerRef.current || typeof d3 === "undefined") return;
    const container = containerRef.current;
    d3.select(container).selectAll(".traversal-ring").remove();
    d3.select(container).selectAll(".traversal-edge").remove();
    if (!traversal || !traversal.root_id) return;

    const depthMap: Record<string, number> = {};
    depthMap[traversal.root_id] = 0;
    for (const n of traversal.nodes) depthMap[n.id] = n.depth;
    const maxDepth = Math.max(0, ...Object.values(depthMap));
    const edgesByDepth: Record<number, typeof traversal.edges> = {};
    for (const e of traversal.edges) {
      const d = depthMap[e.to] ?? 1;
      (edgesByDepth[d] = edgesByDepth[d] || []).push(e);
    }

    const trG = d3.select(container).select("svg").select("g");
    const revealDepth = (depth: number) => {
      const col = TRAVERSAL_DEPTH_COLORS[Math.min(depth, TRAVERSAL_DEPTH_COLORS.length - 1)];
      d3.select(container).selectAll(".graph-node").each(function(this: any, d: any) {
        if (depthMap[d.id] !== depth) return;
        d3.select(this).append("circle")
          .attr("class", "traversal-ring").attr("r", d.r + (depth === 0 ? 12 : Math.max(4, 9 - depth)))
          .attr("fill", "none").attr("stroke", col)
          .attr("stroke-width", depth === 0 ? 2.5 : 1.5)
          .attr("stroke-dasharray", depth === 0 ? null : "4 2")
          .attr("opacity", 0).attr("pointer-events", "none")
          .transition().duration(350).attr("opacity", Math.max(0.3, 1 - depth * 0.18));
      });
      if (!trG.empty()) {
        const positions = nodePositionsRef.current;
        for (const e of (edgesByDepth[depth] || [])) {
          const src = positions[e.from], dst = positions[e.to];
          if (!src || !dst) continue;
          trG.append("line").attr("class", "traversal-edge")
            .attr("x1", src.x).attr("y1", src.y).attr("x2", src.x).attr("y2", src.y)
            .attr("stroke", col).attr("stroke-width", 2).attr("stroke-dasharray", "6 3")
            .attr("opacity", 0.7).attr("pointer-events", "none").lower()
            .transition().duration(400).attr("x2", dst.x).attr("y2", dst.y);
        }
      }
    };

    const STEP_MS = 650;
    for (let d = 0; d <= maxDepth; d++) {
      const timer = setTimeout(() => revealDepth(d), d * STEP_MS);
      _traversalTimersRef.current.push(timer);
    }
    return () => { _traversalTimersRef.current.forEach(t => clearTimeout(t)); _traversalTimersRef.current = []; };
  }, [traversal, graphData]);

  useEffect(() => {
    if (!containerRef.current || !graphData || typeof d3 === "undefined") return;
    const container = containerRef.current;
    const W = container.clientWidth || 900, H = container.clientHeight || 600;
    d3.select(container).selectAll("*").remove();
    setTooltip(null);

    const hasPcaLayout = graphData.nodes.some((n: any) => n.px != null);
    const pcaSpread = Math.min(W, H) * 0.42;

    const nodes = graphData.nodes.map((n: any) => {
      const node = { ...n, r: nodeRadius(n.citedBy), color: nodeColor(n.topics) };
      if (hasPcaLayout && n.px != null) {
        node.fx = W / 2 + n.px * pcaSpread; node.fy = H / 2 + n.py * pcaSpread;
        node.x = node.fx; node.y = node.fy;
      }
      return node;
    });
    const nodeById: Record<string, any> = Object.fromEntries(nodes.map((n: any) => [n.id, n]));
    nodeByIdRef.current = nodeById;

    const links = graphData.edges
      .filter((e: any) => nodeById[e.from] && nodeById[e.to])
      .map((e: any) => ({ source: e.from, target: e.to, influential: e.influential, type: "citation" }));

    const authIdx: Record<string, any[]> = {};
    (graphData.author_edges || []).forEach((e: any) => {
      if (!authIdx[e.from]) authIdx[e.from] = [];
      if (!authIdx[e.to]) authIdx[e.to] = [];
      authIdx[e.from].push({ id: e.to, shared: e.shared || [] });
      authIdx[e.to].push({ id: e.from, shared: e.shared || [] });
    });
    authorEdgeIndexRef.current = authIdx;

    const llmLinksAll = (graphData.llm_edges || [])
      .filter((e: any) => nodeById[e.from] && nodeById[e.to])
      .map((e: any) => ({ source: e.from, target: e.to, type: "llm", edge_type: e.edge_type, description: e.description, confidence: e.confidence ?? 0 }));
    llmLinksDataRef.current = llmLinksAll;

    const _applyLlmFilter = (sel: any, data: any[], k: number) => {
      const countByNode: Record<string, number> = {};
      const visible = new Set();
      const sorted = [...data].sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0));
      for (const e of sorted) {
        const src = typeof e.source === "object" ? e.source.id : e.source;
        countByNode[src] = (countByNode[src] || 0) + 1;
        if (countByNode[src] <= k) visible.add(e);
      }
      sel.attr("display", (d: any) => visible.has(d) ? null : "none");
    };

    const allLinks = [...links, ...llmLinksAll];
    setStats({ nodes: nodes.length, edges: links.length, author: 0, llm: llmLinksAll.length });

    const svg = d3.select(container).append("svg").attr("width", "100%").attr("height", "100%").style("display", "block");
    const zoom = d3.zoom().scaleExtent([0.05, 12]).on("zoom", (ev: any) => g.attr("transform", ev.transform));
    svg.call(zoom);
    svg.on("dblclick.zoom", null);
    svg.on("click", () => setGraphSelectedId(null));
    zoomRef.current = { zoom, svg };

    const defs = svg.append("defs");
    const mkArrow = (id: string, color: string, size: number) =>
      defs.append("marker").attr("id", id).attr("viewBox", "0 0 8 8").attr("refX", 8).attr("refY", 4)
        .attr("markerWidth", size).attr("markerHeight", size).attr("orient", "auto")
        .append("path").attr("d", "M0,1 L8,4 L0,7 Z").attr("fill", color);
    mkArrow("arr-dim", "rgba(36,28,18,0.18)", 5);
    mkArrow("arr-active", "#c8712a", 6);
    mkArrow("arr-inbound", "#b89020", 6);

    const g = svg.append("g");
    const sim = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(allLinks).id((d: any) => d.id)
        .distance((d: any) => d.type === "semantic" ? 60 : d.influential ? 55 : 85)
        .strength((d: any) => hasPcaLayout ? 0 : (d.type === "semantic" ? 0.15 : 0.35)))
      .force("charge", hasPcaLayout ? null : d3.forceManyBody().strength((d: any) => -(d.r * 28)).distanceMax(350))
      .force("center", hasPcaLayout ? null : d3.forceCenter(W / 2, H / 2).strength(0.06))
      .force("collide", hasPcaLayout ? null : d3.forceCollide((d: any) => d.r + 5).strength(0.75).iterations(2))
      .alphaDecay(0.018).velocityDecay(0.38);

    const semNeighborGroup = g.append("g").attr("class", "g-sem-neighbors");
    semNeighborGroupRef.current = semNeighborGroup;

    const authorLinkSel = g.append("g").attr("class", "g-author-links")
      .selectAll("line").data([]).join("line");

    const llmLinkSel = g.append("g").attr("class", "g-llm-links")
      .selectAll("line").data(llmLinksAll).join("line")
      .attr("stroke", (d: any) => LLM_EDGE_COLORS[d.edge_type] || "#8a7f74")
      .attr("stroke-width", 1.4).attr("stroke-opacity", 0.55);
    llmLinkSelRef.current = llmLinkSel;
    _applyLlmFilter(llmLinkSel, llmLinksAll, classifyNeighbors);

    const linkSel = g.append("g").attr("class", "g-links")
      .selectAll("line").data(links).join("line")
      .attr("class", "graph-edge").attr("stroke", "rgba(36,28,18,0.14)")
      .attr("stroke-width", (d: any) => d.influential ? 1.6 : 0.7)
      .attr("marker-end", "url(#arr-dim)");

    const applyActiveStyles = (linkS: any, nodeS: any, aid: string | null) => {
      linkS.attr("stroke", (l: any) => {
        if (!l.source || !l.target) return "rgba(36,28,18,0.14)";
        const sid = l.source.id || l.source, tid = l.target.id || l.target;
        if (aid && sid === aid) return "#c8712a";
        if (aid && tid === aid) return "#b89020";
        return "rgba(36,28,18,0.10)";
      }).attr("stroke-width", (l: any) => {
        if (!l.source || !l.target) return 0.7;
        const sid = l.source.id || l.source, tid = l.target.id || l.target;
        return (aid && (sid === aid || tid === aid)) ? 1.8 : 0.6;
      }).attr("marker-end", (l: any) => {
        if (!l.source || !l.target) return "url(#arr-dim)";
        const sid = l.source.id || l.source, tid = l.target.id || l.target;
        if (aid && sid === aid) return "url(#arr-active)";
        if (aid && tid === aid) return "url(#arr-inbound)";
        return "url(#arr-dim)";
      });
      nodeS.classed("active", (d: any) => d.id === aid);
      if (!highlightModeRef.current) {
        nodeS.select("circle.nc").attr("opacity", (d: any) => !aid || d.id === aid ? 1 : 0.5);
      }
    };

    const zoomToNode = (id: string) => {
      const pos = nodePositionsRef.current[id];
      if (!pos || !zoomRef.current || !containerRef.current) return;
      const CW = containerRef.current.clientWidth || 900, CH = containerRef.current.clientHeight || 600;
      const scale = 2.5;
      zoomRef.current.svg.transition().duration(650).call(
        zoomRef.current.zoom.transform,
        d3.zoomIdentity.translate(CW / 2 - pos.x * scale, CH / 2 - pos.y * scale).scale(scale),
      );
    };

    const nodeSel = g.append("g").attr("class", "g-nodes")
      .selectAll("g").data(nodes).join("g")
      .attr("class", "graph-node").style("cursor", "pointer")
      .call(d3.drag()
        .on("start", (ev: any, d: any) => { if (!ev.active && !hasPcaLayout) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag", (ev: any, d: any) => { d.fx = ev.x; d.fy = ev.y; })
        .on("end", (ev: any, d: any) => { if (!ev.active && !hasPcaLayout) sim.alphaTarget(0); if (!hasPcaLayout) { d.fx = null; d.fy = null; } }),
      )
      .on("click", (ev: any, d: any) => { ev.stopPropagation(); onSelect(d.id); setGraphSelectedId(d.id); })
      .on("mouseenter", (ev: any, d: any) => {
        const rect = container.getBoundingClientRect();
        setTooltip({ x: ev.clientX - rect.left, y: ev.clientY - rect.top, node: d });
        linkSel
          .attr("stroke", (l: any) => l.source.id === d.id || l.target.id === d.id ? "#c8712a" : "rgba(36,28,18,0.08)")
          .attr("stroke-width", (l: any) => l.source.id === d.id || l.target.id === d.id ? 1.8 : 0.5)
          .attr("marker-end", (l: any) => l.source.id === d.id ? "url(#arr-active)" : l.target.id === d.id ? "url(#arr-inbound)" : "url(#arr-dim)");
      })
      .on("mousemove", (ev: any) => {
        const rect = container.getBoundingClientRect();
        setTooltip((t: any) => t ? { ...t, x: ev.clientX - rect.left, y: ev.clientY - rect.top } : t);
      })
      .on("mouseleave", () => { setTooltip(null); applyActiveStyles(linkSel, nodeSel, activeIdRef.current); });

    nodeSel.append("circle").attr("class", "ring").attr("r", (d: any) => d.r + 7)
      .attr("fill", "none").attr("stroke", "var(--rust)").attr("stroke-width", 2.5).attr("pointer-events", "none");
    nodeSel.append("circle").attr("class", "nc").attr("r", (d: any) => d.r)
      .attr("fill", (d: any) => d.color).attr("stroke", "rgba(247,242,232,0.7)").attr("stroke-width", 1.2);

    nodePositionsRef.current = {};
    const applyPositions = () => {
      [linkSel, authorLinkSel, llmLinkSel].forEach(sel =>
        sel.attr("x1", (d: any) => d.source.x).attr("y1", (d: any) => d.source.y)
           .attr("x2", (d: any) => d.target.x).attr("y2", (d: any) => d.target.y));
      nodeSel.attr("transform", (d: any) => `translate(${d.x},${d.y})`);
      nodes.forEach((n: any) => { nodePositionsRef.current[n.id] = { x: n.x, y: n.y }; });
    };

    const redrawOverlay = (aid: string | null) => {
      if (!aid || !semNeighborGroupRef.current) return;
      semNeighborGroupRef.current.selectAll("*").remove();
      if (activeNeighborsRef.current.length > 0) {
        const { min, max } = simRangeRef.current;
        drawSemNeighbors(semNeighborGroupRef.current, nodePositionsRef.current, aid, activeNeighborsRef.current, min, max);
      } else {
        api.fetchNeighbors(aid, embedStatusRef.current.embedded || 1000).then(raw => {
          const neighbors = raw as unknown[];
          if (activeIdRef.current !== aid || !neighbors?.length || !semNeighborGroupRef.current) return;
          activeNeighborsRef.current = neighbors;
          const { min, max } = simRangeRef.current;
          drawSemNeighbors(semNeighborGroupRef.current, nodePositionsRef.current, aid, neighbors, min, max);
        }).catch(() => {});
      }
    };

    if (hasPcaLayout) {
      sim.stop(); simSettledRef.current = true; sim.tick(1);
      nodes.forEach((n: any) => { if (n.fx != null) { n.x = n.fx; n.y = n.fy; } });
      applyPositions();
      const pcaAid = activeIdRef.current;
      if (pcaAid) zoomToNode(pcaAid);
      redrawOverlay(pcaAid);
    } else {
      simSettledRef.current = false;
      sim.on("tick", applyPositions);
      sim.on("end", () => { simSettledRef.current = true; const aid = activeIdRef.current; if (aid) zoomToNode(aid); redrawOverlay(aid); });
    }

    applyActiveStyles(linkSel, nodeSel, activeId);
    _applyHighlight(activeId);
    return () => sim.stop();
  }, [graphData]);

  useEffect(() => {
    activeIdRef.current = activeId;
    if (!containerRef.current || typeof d3 === "undefined") return;
    const root = d3.select(containerRef.current);
    const linkSel = root.selectAll(".graph-edge"), nodeSel = root.selectAll(".graph-node");

    const applyActiveStyles = (linkS: any, nodeS: any, aid: string | null) => {
      linkS.attr("stroke", (l: any) => {
        if (!l.source || !l.target) return "rgba(36,28,18,0.14)";
        const sid = l.source.id || l.source, tid = l.target.id || l.target;
        if (aid && sid === aid) return "#c8712a"; if (aid && tid === aid) return "#b89020";
        return "rgba(36,28,18,0.10)";
      }).attr("stroke-width", (l: any) => {
        if (!l.source || !l.target) return 0.7;
        const sid = l.source.id || l.source, tid = l.target.id || l.target;
        return (aid && (sid === aid || tid === aid)) ? 1.8 : 0.6;
      }).attr("marker-end", (l: any) => {
        if (!l.source || !l.target) return "url(#arr-dim)";
        const sid = l.source.id || l.source, tid = l.target.id || l.target;
        if (aid && sid === aid) return "url(#arr-active)"; if (aid && tid === aid) return "url(#arr-inbound)";
        return "url(#arr-dim)";
      });
      nodeS.classed("active", (d: any) => d.id === aid);
      if (!highlightModeRef.current) nodeS.select("circle.nc").attr("opacity", (d: any) => !aid || d.id === aid ? 1 : 0.5);
    };

    const zoomToNode = (id: string) => {
      const pos = nodePositionsRef.current[id];
      if (!pos || !zoomRef.current || !containerRef.current) return;
      const CW = containerRef.current.clientWidth || 900, CH = containerRef.current.clientHeight || 600;
      const scale = 2.5;
      zoomRef.current.svg.transition().duration(650).call(
        zoomRef.current.zoom.transform,
        d3.zoomIdentity.translate(CW / 2 - pos.x * scale, CH / 2 - pos.y * scale).scale(scale),
      );
    };

    applyActiveStyles(linkSel, nodeSel, activeId);
    _applyHighlight(activeId);
    activeNeighborsRef.current = [];
    if (semNeighborGroupRef.current) semNeighborGroupRef.current.selectAll("*").remove();
    if (activeId) {
      zoomToNode(activeId);
      api.fetchNeighbors(activeId, embedStatusRef.current.embedded || 1000).then(raw => {
        const neighbors = raw as unknown[];
        if (activeIdRef.current !== activeId || !neighbors?.length || !semNeighborGroupRef.current) return;
        activeNeighborsRef.current = neighbors;
        if (simSettledRef.current) {
          const { min, max } = simRangeRef.current;
          drawSemNeighbors(semNeighborGroupRef.current, nodePositionsRef.current, activeId, neighbors, min, max);
        }
        window.dispatchEvent(new CustomEvent("rs:journey-action", { detail: { paperId: activeId, action: "semantic" } }));
      }).catch(() => {});
    }
  }, [activeId]);

  useEffect(() => {
    simRangeRef.current = { min: simMin, max: simMax };
    const grp = semNeighborGroupRef.current;
    if (!grp) return;
    grp.selectAll("*").remove();
    if (!activeId) return;
    drawSemNeighbors(grp, nodePositionsRef.current, activeId, activeNeighborsRef.current, simMin, simMax);
  }, [simMin, simMax]);

  const zoomIn = () => zoomRef.current?.svg.transition().duration(300).call(zoomRef.current.zoom.scaleBy, 1.5);
  const zoomOut = () => zoomRef.current?.svg.transition().duration(300).call(zoomRef.current.zoom.scaleBy, 0.67);
  const zoomFit = () => zoomRef.current?.svg.transition().duration(400).call(zoomRef.current.zoom.transform, d3.zoomIdentity.translate(0, 0).scale(1));

  return (
    <div className="graph-wrap">
      <div className="graph-controls">
        <div className="legend">
          <h4>Graph</h4>
          {TOPIC_LABEL_COLORS.map(({ color, label }) => (
            <div key={label} className="lg-row">
              <span className="sw" style={{ background: color, borderRadius: "50%" }} />
              {label}
            </div>
          ))}
          <div style={{ marginTop: 8, color: "var(--ink-4)", fontSize: 10, lineHeight: 1.7 }}>
            Size ∝ citation count<br />
            <span style={{ color: "#3a8a6a" }}>─ ─</span> Semantic&nbsp;
            Drag · Scroll to zoom
          </div>
        </div>
        <div className="zoom">
          <button title="Zoom in" onClick={zoomIn}>+</button>
          <button title="Zoom out" onClick={zoomOut}>−</button>
          <button title="Fit" onClick={zoomFit}>▣</button>
          <button title="Refresh graph" onClick={() => { delete _graphCache[_cacheKey(layoutMode, showAuthor, showLlm)]; fetchGraph(layoutMode, showAuthor, showLlm, true); }} style={{ fontSize: 13 }}>↺</button>
        </div>
        <div className="legend" style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--rule)" }}>
          <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.07em", color: "var(--ink-3)", marginBottom: 6 }}>LAYOUT</div>
          {[
            { id: "force", label: "Force", title: "Physics-based force simulation" },
            { id: "pca",   label: "PCA",   title: "2D PCA projection of abstract embeddings" },
            { id: "umap",  label: "UMAP",  title: "UMAP projection — better cluster separation (slower)" },
          ].map(({ id, label, title }) => (
            <button key={id} className={"graph-sem-toggle" + (layoutMode === id ? " on" : "")}
              onClick={() => handleLayoutChange(id)} title={title} style={{ marginBottom: 3 }}>
              <span className="sw" style={{ background: layoutMode === id ? "#5a6abf" : "var(--bg-3)", borderRadius: 2 }} />
              {label}
            </button>
          ))}

          <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.07em", color: "var(--ink-3)", margin: "10px 0 6px" }}>EDGES</div>
          <div style={{ marginBottom: 8 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
              <span style={{ fontSize: 10, color: "var(--ink-4)" }}>Similarity range</span>
              <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--rust)", fontWeight: 600 }}>
                {simMin.toFixed(2)} – {simMax.toFixed(2)}
              </span>
            </div>
            <DualRange min={-1} max={1} step={0.05} valueMin={simMin} valueMax={simMax}
              onChange={(lo, hi) => { setSimMin(lo); setSimMax(hi); _graphState.simMin = lo; _graphState.simMax = hi; }} />
          </div>
          <button className={"graph-sem-toggle" + (showLlm ? " on" : "")} onClick={handleLlmToggle}
            title="LLM-classified relationship types" style={{ marginBottom: 6 }}>
            <span className="sw" style={{ background: showLlm ? "#5a8af0" : "var(--bg-3)", borderRadius: 2 }} />
            LLM-typed ──
          </button>
          {showLlm && (
            <div style={{ margin: "2px 0 8px 18px", fontSize: 9, lineHeight: 1.8, color: "var(--ink-3)" }}>
              {Object.entries(LLM_EDGE_COLORS).map(([t, c]) => (
                <div key={t} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  <span style={{ width: 16, height: 2, background: c, display: "inline-block", borderRadius: 1 }} />
                  {t}
                </div>
              ))}
            </div>
          )}

          <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.07em", color: "var(--ink-3)", margin: "10px 0 6px" }}>HIGHLIGHT</div>
          {[
            { key: "manual", label: "Manual", color: "var(--sulfur)", title: "Papers added manually or by the agent" },
            { key: "paper",  label: "Papers", color: "var(--rust)",   title: "All non-Wikipedia, non-web papers" },
            { key: "wiki",   label: "Wiki",   color: "#4db6ac",       title: "Wikipedia articles" },
            { key: "web",    label: "Web",    color: "#7b9fd4",       title: "Ingested web pages" },
          ].map(opt => (
            <button key={opt.key} className={"graph-sem-toggle" + (highlightMode === opt.key ? " on" : "")}
              onClick={() => setHighlightMode(prev => prev === opt.key ? null : opt.key)}
              title={opt.title} style={{ marginBottom: 4 }}>
              <span className="sw" style={{ background: highlightMode === opt.key ? opt.color : "var(--bg-3)", borderRadius: 2 }} />
              {opt.label}
            </button>
          ))}

          <div style={{ fontSize: 10, color: "var(--ink-4)", marginBottom: 4 }}>
            {embedStatus.embedded}/{embedStatus.total} embedded
          </div>
          <button className="ghost small" style={{ width: "100%", justifyContent: "center", fontSize: 10, marginBottom: 4 }}
            onClick={handleEmbedAll} disabled={embedding || embedStatus.embedded >= embedStatus.total}>
            {embedding ? <>
              <span style={{ display: "inline-block", width: 8, height: 8, border: "1.5px solid var(--rust)", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite", marginRight: 4 }} />
              Embedding…
            </> : "⊙ Embed corpus"}
          </button>
          <div style={{ margin: "4px 0 2px", fontSize: 9, color: "var(--ink-4)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 1 }}>
              <span>top-k neighbors</span><span style={{ color: "var(--ink-2)", fontWeight: 600 }}>{classifyNeighbors}</span>
            </div>
            <input type="range" min={1} max={10} step={1} value={classifyNeighbors}
              onChange={e => setClassifyNeighbors(+e.target.value)}
              style={{ width: "100%", accentColor: "var(--rust)", cursor: "pointer" }} />
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 4 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 5, cursor: "pointer", userSelect: "none" }}>
                <input type="checkbox" checked={classifyAll} onChange={e => setClassifyAll(e.target.checked)}
                  style={{ accentColor: "var(--rust)", cursor: "pointer" }} />
                <span>all sources</span>
              </label>
            </div>
          </div>
          <div style={{ display: "flex", gap: 4 }}>
            {classifying ? (
              <>
                <button className="ghost small" style={{ flex: 1, justifyContent: "center", fontSize: 10 }} disabled>
                  <span style={{ display: "inline-block", width: 8, height: 8, border: "1.5px solid var(--rust)", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite", marginRight: 4 }} />
                  {classifyStatus?.done ?? 0}/{classifyStatus?.total ?? "…"}
                </button>
                <button className="ghost small" style={{ fontSize: 10, padding: "3px 7px", color: "var(--rust)" }}
                  onClick={handleStopClassify}>■</button>
              </>
            ) : (
              <button className="ghost small" style={{ flex: 1, justifyContent: "center", fontSize: 10 }}
                onClick={handleClassify} disabled={embedStatus.embedded === 0 && !classifyAll}>
                ⊛ Classify
              </button>
            )}
            <button className="ghost small" style={{ fontSize: 10, padding: "3px 7px" }}
              onClick={handleOpenEdgeInspector}>☰</button>
          </div>
        </div>
      </div>

      <div className="graph-meta">
        <span><b>{stats.nodes}</b> nodes</span>
        <span><b>{stats.edges}</b> citations</span>
        {showLlm && <span style={{ color: "#5a8af0" }}><b>{stats.llm}</b> llm</span>}
        {layoutMode !== "force" && <span style={{ color: "#5a6abf" }}>{layoutMode.toUpperCase()}</span>}
        {loading && <span style={{ color: "var(--rust)" }}>loading…</span>}
      </div>

      {loading && (
        <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 12, background: "var(--bg)", zIndex: 10 }}>
          <div style={{ width: 28, height: 28, border: "2.5px solid var(--rust)", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
          <div style={{ fontSize: 12, color: "var(--ink-4)" }}>Building graph…</div>
        </div>
      )}

      <div ref={containerRef} style={{ width: "100%", height: "100%", position: "absolute", inset: 0 }} />

      {graphSelectedId && (() => {
        const selNode = nodeByIdRef.current[graphSelectedId];
        const myLlmEdges = llmLinksDataRef.current.filter(e => {
          const sid = typeof e.source === "object" ? e.source.id : e.source;
          const tid = typeof e.target === "object" ? e.target.id : e.target;
          return sid === graphSelectedId || tid === graphSelectedId;
        }).map(e => {
          const sid = typeof e.source === "object" ? e.source.id : e.source;
          const otherId = sid === graphSelectedId
            ? (typeof e.target === "object" ? e.target.id : e.target) : sid;
          return { ...e, otherId };
        }).sort((a: any, b: any) => (b.confidence ?? 0) - (a.confidence ?? 0));

        const filteredNeighbors = (graphNodeNeighbors || [])
          .filter((n: any) => n.similarity >= simMin && n.similarity <= simMax)
          .slice(0, classifyNeighbors);

        return (
          <div className="graph-node-detail" onClick={e => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, lineHeight: 1.35, flex: 1, paddingRight: 8 }}>
                {selNode?.title || graphSelectedId}
              </div>
              <button className="ghost small" style={{ fontSize: 10, padding: "2px 5px", flexShrink: 0 }}
                onClick={() => setGraphSelectedId(null)}>✕</button>
            </div>
            {selNode && (
              <div style={{ fontSize: 9, color: "var(--ink-4)", marginBottom: 8 }}>
                {selNode.source} · {selNode.year || selNode.date?.slice(0, 4)}
                {selNode.citedBy > 0 && ` · ${selNode.citedBy} citations`}
              </div>
            )}
            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--ink-3)", letterSpacing: "0.06em", marginBottom: 4 }}>
              SEMANTIC NEIGHBORS <span style={{ fontWeight: 400, color: "var(--ink-4)", marginLeft: 4 }}>
                ({simMin.toFixed(2)}–{simMax.toFixed(2)}, k={classifyNeighbors})
              </span>
            </div>
            {graphNodeNeighbors === null
              ? <div style={{ fontSize: 9, color: "var(--ink-4)", marginBottom: 8 }}>Loading…</div>
              : filteredNeighbors.length === 0
                ? <div style={{ fontSize: 9, color: "var(--ink-4)", marginBottom: 8 }}>None in range</div>
                : <div style={{ marginBottom: 10 }}>
                    {filteredNeighbors.map((n: any, i: number) => {
                      const nd = nodeByIdRef.current[n.id];
                      return (
                        <div key={n.id} style={{ display: "flex", gap: 6, alignItems: "flex-start", padding: "3px 0",
                          borderBottom: i < filteredNeighbors.length - 1 ? "1px solid var(--rule)" : "none" }}>
                          <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--rust)", minWidth: 32, paddingTop: 1 }}>
                            {n.similarity.toFixed(2)}
                          </span>
                          <span style={{ fontSize: 9.5, color: "var(--ink-2)", lineHeight: 1.3, cursor: "pointer" }}
                            onClick={() => { onSelect(n.id); setGraphSelectedId(n.id); }}>
                            {nd?.title || n.id}
                          </span>
                        </div>
                      );
                    })}
                  </div>
            }
            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--ink-3)", letterSpacing: "0.06em", marginBottom: 4 }}>
              TYPED RELATIONSHIPS
            </div>
            {myLlmEdges.length === 0
              ? <div style={{ fontSize: 9, color: "var(--ink-4)" }}>None classified yet</div>
              : myLlmEdges.map((e: any, i: number) => {
                  const nd = nodeByIdRef.current[e.otherId];
                  const sid = typeof e.source === "object" ? e.source.id : e.source;
                  const isFrom = sid === graphSelectedId;
                  const col = LLM_EDGE_COLORS[e.edge_type] || "#8a7f74";
                  return (
                    <div key={i} style={{ padding: "4px 0", borderBottom: i < myLlmEdges.length - 1 ? "1px solid var(--rule)" : "none" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 2 }}>
                        <span style={{ fontSize: 8.5, fontWeight: 600, color: col, background: col + "22", borderRadius: 2, padding: "1px 5px" }}>
                          {e.edge_type}
                        </span>
                        <span style={{ fontSize: 8.5, color: "var(--ink-4)" }}>{isFrom ? "→" : "←"}</span>
                        <span style={{ fontSize: 9, color: "var(--ink-3)", fontFamily: "var(--font-mono)" }}>
                          {e.confidence != null ? e.confidence.toFixed(2) : ""}
                        </span>
                      </div>
                      <div style={{ fontSize: 9, color: "var(--ink-2)", lineHeight: 1.3, cursor: "pointer", marginBottom: e.description ? 2 : 0 }}
                        onClick={() => { onSelect(e.otherId); setGraphSelectedId(e.otherId); }}>
                        {nd?.title || e.otherId}
                      </div>
                      {e.description && (
                        <div style={{ fontSize: 8.5, color: "var(--ink-4)", lineHeight: 1.3, fontStyle: "italic" }}>{e.description}</div>
                      )}
                    </div>
                  );
                })
            }
          </div>
        );
      })()}

      {showEdgeInspector && (() => {
        const types = ["all", ...Object.keys(LLM_EDGE_COLORS)];
        const countByNode: Record<string, number> = {};
        const sliderVisible = new Set<any>();
        const sortedRows = [...edgeRows].sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0));
        for (const e of sortedRows) {
          countByNode[e.from_id] = (countByNode[e.from_id] || 0) + 1;
          if ((e.confidence ?? 0) >= 0.5 && countByNode[e.from_id] <= classifyNeighbors) sliderVisible.add(e);
        }
        const filtered = edgeRows.filter(e => {
          if (!sliderVisible.has(e)) return false;
          if (edgeFilter !== "all" && e.edge_type !== edgeFilter) return false;
          if (edgeSearch) {
            const q = edgeSearch.toLowerCase();
            return (e.from_title + e.to_title + (e.description || "")).toLowerCase().includes(q);
          }
          return true;
        });
        return (
          <div style={{ position: "absolute", inset: 0, zIndex: 50, background: "rgba(36,28,18,0.55)", display: "flex", alignItems: "flex-start", justifyContent: "center", paddingTop: 40 }}
            onClick={() => setShowEdgeInspector(false)}>
            <div onClick={e => e.stopPropagation()} style={{ background: "var(--bg)", border: "1px solid var(--rule-2)", borderRadius: 10, width: "min(820px, 95vw)", maxHeight: "78vh", display: "flex", flexDirection: "column", boxShadow: "0 8px 32px rgba(36,28,18,0.22)" }}>
              <div style={{ padding: "14px 20px 12px", borderBottom: "1px solid var(--rule)", display: "flex", alignItems: "center", gap: 12, flexShrink: 0 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 14, fontWeight: 700 }}>
                    LLM-classified edges
                    <span style={{ fontWeight: 400, fontSize: 11, color: "var(--ink-4)", marginLeft: 8 }}>
                      {filtered.length} shown · {sliderVisible.size} in range · {edgeRows.length} total
                    </span>
                  </div>
                </div>
                <input value={edgeSearch} onChange={e => setEdgeSearch(e.target.value)} placeholder="search…"
                  style={{ padding: "5px 10px", borderRadius: 5, border: "1px solid var(--rule-2)", fontSize: 11, background: "var(--bg-1)", color: "var(--ink)", outline: "none", width: 220 }} />
                <button className="ghost" style={{ fontSize: 11, padding: "4px 10px" }}
                  onClick={() => api.deleteEdges("llm").then(() => {
                    setEdgeRows([]); setShowEdgeInspector(false);
                    delete _graphCache[_cacheKey(layoutMode, showAuthor, true)];
                    if (showLlm) fetchGraph(layoutMode, showAuthor, true, true);
                  })}>Clear all</button>
                <button onClick={() => setShowEdgeInspector(false)}
                  style={{ background: "none", border: "none", fontSize: 16, cursor: "pointer", color: "var(--ink-4)", padding: "0 4px" }}>✕</button>
              </div>
              <div style={{ display: "flex", gap: 4, padding: "8px 20px", borderBottom: "1px solid var(--rule)", flexShrink: 0, flexWrap: "wrap" }}>
                {types.map(t => (
                  <button key={t} onClick={() => setEdgeFilter(t)} style={{
                    padding: "3px 10px", borderRadius: 20, fontSize: 10, fontWeight: 600, cursor: "pointer", border: "1px solid",
                    borderColor: edgeFilter === t ? (LLM_EDGE_COLORS[t] || "var(--rust)") : "var(--rule-2)",
                    background: edgeFilter === t ? `color-mix(in srgb, ${LLM_EDGE_COLORS[t] || "var(--rust)"} 12%, transparent)` : "var(--bg-1)",
                    color: edgeFilter === t ? (LLM_EDGE_COLORS[t] || "var(--rust)") : "var(--ink-4)",
                  }}>
                    {t === "all" ? `all · ${sliderVisible.size}` : `${t} · ${[...sliderVisible].filter((e: any) => e.edge_type === t).length}`}
                  </button>
                ))}
              </div>
              <div style={{ overflowY: "auto", flex: 1 }}>
                {filtered.length === 0
                  ? <div style={{ padding: 32, textAlign: "center", color: "var(--ink-4)", fontSize: 12 }}>No edges found.</div>
                  : <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                      <thead>
                        <tr style={{ position: "sticky", top: 0, background: "var(--bg-1)", zIndex: 1 }}>
                          {["Paper A", "Type", "Paper B", "Description", "Sim"].map(h => (
                            <th key={h} style={{ padding: "7px 14px", textAlign: "left", color: "var(--ink-4)", fontWeight: 600, fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5, borderBottom: "1px solid var(--rule-2)" }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {filtered.map((e: any, i: number) => (
                          <tr key={i} style={{ borderBottom: "1px solid var(--rule)" }}>
                            <td style={{ padding: "8px 14px", lineHeight: 1.4, verticalAlign: "top" }}>
                              <span style={{ fontSize: 9, fontFamily: "var(--font-mono)", color: "var(--ink-5)", display: "block" }}>{e.from_id}</span>
                              {e.from_title}
                            </td>
                            <td style={{ padding: "8px 6px", verticalAlign: "top", textAlign: "center" }}>
                              <span style={{ display: "inline-block", padding: "2px 7px", borderRadius: 20, fontSize: 9, fontWeight: 700, background: `color-mix(in srgb, ${LLM_EDGE_COLORS[e.edge_type] || "#9A8C73"} 14%, transparent)`, color: LLM_EDGE_COLORS[e.edge_type] || "#9A8C73", border: `1px solid color-mix(in srgb, ${LLM_EDGE_COLORS[e.edge_type] || "#9A8C73"} 30%, transparent)`, whiteSpace: "nowrap" }}>
                                {e.edge_type}
                              </span>
                            </td>
                            <td style={{ padding: "8px 14px", lineHeight: 1.4, verticalAlign: "top" }}>
                              <span style={{ fontSize: 9, fontFamily: "var(--font-mono)", color: "var(--ink-5)", display: "block" }}>{e.to_id}</span>
                              {e.to_title}
                            </td>
                            <td style={{ padding: "8px 14px", color: "var(--ink-3)", lineHeight: 1.5, verticalAlign: "top", fontStyle: e.description ? "normal" : "italic" }}>{e.description || "—"}</td>
                            <td style={{ padding: "8px 10px", color: "var(--ink-4)", verticalAlign: "top", textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 10 }}>{e.confidence != null ? e.confidence.toFixed(2) : "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                }
              </div>
            </div>
          </div>
        );
      })()}

      {tooltip && (
        <div style={{ position: "absolute", left: tooltip.x + 14, top: tooltip.y - 10, pointerEvents: "none", zIndex: 20, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 6, padding: "8px 12px", maxWidth: 280, boxShadow: "0 2px 12px rgba(0,0,0,0.18)" }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4, lineHeight: 1.4 }}>{tooltip.node.title}</div>
          <div style={{ fontSize: 10, color: "var(--ink-4)", display: "flex", gap: 10, flexWrap: "wrap" }}>
            {tooltip.node.venue && <span>{tooltip.node.venue}</span>}
            {tooltip.node.date && <span>{tooltip.node.date?.slice(0, 7)}</span>}
            <span>{tooltip.node.citedBy} cited-by</span>
            {(tooltip.node.topics || []).slice(0, 2).map((t: string) => (
              <span key={t} style={{ background: "var(--bg-2)", padding: "1px 5px", borderRadius: 3 }}>{t}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
