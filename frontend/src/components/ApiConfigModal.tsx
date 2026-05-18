import { useState, useEffect, useMemo } from "react";
import { api } from "../api";

const LLM_PROVIDERS = [
  { id: "ollama",    label: "Ollama",   hint: "Local · free · private",    needsKey: false, needsUrl: true,  urlLabel: "Ollama URL",   urlDefault: "http://localhost:11434",  keyField: "" },
  { id: "vllm",     label: "vLLM",     hint: "Local · fast · GPU needed", needsKey: false, needsUrl: true,  urlLabel: "vLLM URL",     urlDefault: "http://localhost:8000/v1", keyField: "" },
  { id: "anthropic", label: "Claude",  hint: "API · needs key",           needsKey: true,  needsUrl: false, urlLabel: "",             urlDefault: "",                        keyField: "anthropic_api_key" },
  { id: "deepseek", label: "DeepSeek", hint: "API · needs key",           needsKey: true,  needsUrl: false, urlLabel: "",             urlDefault: "",                        keyField: "deepseek_api_key" },
  { id: "gemini",   label: "Gemini",   hint: "API · needs key",           needsKey: true,  needsUrl: false, urlLabel: "",             urlDefault: "",                        keyField: "gemini_api_key" },
];

const OCR_PROVIDERS = [
  { id: "",       label: "Inherit", hint: "Same provider as LLM" },
  { id: "ollama", label: "Ollama",  hint: "Local · multimodal model" },
  { id: "vllm",   label: "vLLM",   hint: "Fast · GPU · dedicated OCR" },
];

interface ArxivCat { id: string; label: string; group: string; }

const SETTINGS_ARXIV_CATEGORIES: ArxivCat[] = [
  { id: "cs.AI", label: "Artificial Intelligence", group: "cs" },
  { id: "cs.CL", label: "Computation and Language", group: "cs" },
  { id: "cs.CV", label: "Computer Vision", group: "cs" },
  { id: "cs.LG", label: "Machine Learning", group: "cs" },
  { id: "cs.NE", label: "Neural and Evolutionary Computing", group: "cs" },
  { id: "cs.RO", label: "Robotics", group: "cs" },
  { id: "cs.IR", label: "Information Retrieval", group: "cs" },
  { id: "cs.HC", label: "Human-Computer Interaction", group: "cs" },
  { id: "cs.MA", label: "Multiagent Systems", group: "cs" },
  { id: "cs.CR", label: "Cryptography and Security", group: "cs" },
  { id: "cs.DB", label: "Databases", group: "cs" },
  { id: "cs.DC", label: "Distributed Computing", group: "cs" },
  { id: "cs.DS", label: "Data Structures and Algorithms", group: "cs" },
  { id: "cs.GR", label: "Graphics", group: "cs" },
  { id: "cs.GT", label: "Game Theory", group: "cs" },
  { id: "cs.IT", label: "Information Theory", group: "cs" },
  { id: "cs.NA", label: "Numerical Analysis", group: "cs" },
  { id: "cs.NI", label: "Networking and Internet", group: "cs" },
  { id: "cs.PL", label: "Programming Languages", group: "cs" },
  { id: "cs.SE", label: "Software Engineering", group: "cs" },
  { id: "cs.SY", label: "Systems and Control", group: "cs" },
  { id: "stat.ML", label: "Machine Learning", group: "stat" },
  { id: "stat.ME", label: "Methodology", group: "stat" },
  { id: "stat.TH", label: "Statistics Theory", group: "stat" },
  { id: "stat.AP", label: "Applications", group: "stat" },
  { id: "stat.CO", label: "Computation", group: "stat" },
  { id: "math.OC", label: "Optimization and Control", group: "math" },
  { id: "math.ST", label: "Statistics Theory", group: "math" },
  { id: "math.NA", label: "Numerical Analysis", group: "math" },
  { id: "math.PR", label: "Probability", group: "math" },
  { id: "math.IT", label: "Information Theory", group: "math" },
  { id: "eess.AS", label: "Audio and Speech Processing", group: "eess" },
  { id: "eess.IV", label: "Image and Video Processing", group: "eess" },
  { id: "eess.SP", label: "Signal Processing", group: "eess" },
  { id: "eess.SY", label: "Systems and Control", group: "eess" },
  { id: "q-bio.BM", label: "Biomolecules", group: "q-bio" },
  { id: "q-bio.CB", label: "Cell Behavior", group: "q-bio" },
  { id: "q-bio.GN", label: "Genomics", group: "q-bio" },
  { id: "q-bio.NC", label: "Neurons and Cognition", group: "q-bio" },
  { id: "q-bio.QM", label: "Quantitative Methods", group: "q-bio" },
  { id: "q-bio.PE", label: "Populations and Evolution", group: "q-bio" },
  { id: "quant-ph", label: "Quantum Physics", group: "physics" },
  { id: "physics.comp-ph", label: "Computational Physics", group: "physics" },
  { id: "physics.data-an", label: "Data Analysis", group: "physics" },
  { id: "econ.EM", label: "Econometrics", group: "econ" },
  { id: "econ.GN", label: "General Economics", group: "econ" },
  { id: "econ.TH", label: "Theoretical Economics", group: "econ" },
];

const SETTINGS_BIORXIV_CATEGORIES: string[] = [
  "animal behavior and cognition", "biochemistry", "bioengineering",
  "bioinformatics", "biophysics", "cancer biology", "cell biology",
  "clinical trials", "developmental biology", "ecology", "epidemiology",
  "evolutionary biology", "genetics", "genomics", "immunology",
  "microbiology", "molecular biology", "neuroscience", "paleontology",
  "pathology", "pharmacology and toxicology", "physiology", "plant biology",
  "scientific communication and education", "synthetic biology",
  "systems biology", "zoology",
];

const MASKED = "••••••••••••••••";

function ChipInput({ value, onChange, presets, min, max, step = 1, format }: {
  value: number; onChange: (v: number) => void; presets: number[];
  min: number; max: number; step?: number; format?: (v: number) => string;
}) {
  const fmt = format || ((v: number) => String(v));
  const isPreset = presets.includes(value);
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 5, alignItems: "center" }}>
      {presets.map(p => {
        const active = value === p;
        return (
          <button key={p} onClick={() => onChange(p)} style={{
            padding: "4px 10px", borderRadius: 5, fontSize: 12, cursor: "pointer",
            border: active ? "1.5px solid var(--rust)" : "1px solid var(--border)",
            background: active ? "rgba(162,62,34,0.08)" : "var(--surface-2)",
            color: active ? "var(--rust)" : "var(--ink-2)",
            fontFamily: "var(--font-mono)", fontWeight: active ? 600 : 400,
            transition: "all 0.1s",
          }}>
            {fmt(p)}
          </button>
        );
      })}
      <input
        type="number" min={min} max={max} step={step}
        value={value}
        onChange={e => { const n = step < 1 ? parseFloat(e.target.value) : parseInt(e.target.value, 10); if (!isNaN(n)) onChange(n); }}
        style={{
          width: 72, padding: "4px 8px", borderRadius: 5, fontSize: 12,
          border: !isPreset ? "1.5px solid var(--rust)" : "1px solid var(--border)",
          background: "var(--surface-2)", color: !isPreset ? "var(--rust)" : "var(--ink-2)",
          fontFamily: "var(--font-mono)", outline: "none",
        }}
      />
    </div>
  );
}

function KeyField({ label, hint, value, onChange, optional }: { label: string; hint?: string; value: string; onChange: (v: string) => void; optional?: boolean }) {
  const [show, setShow] = useState(false);
  const isSet = value === MASKED;
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-1)" }}>
          {label}
          {!optional && <span style={{ color: "#c0392b", marginLeft: 4 }}>*</span>}
        </span>
        {optional && <span style={{ fontSize: 11, color: "var(--ink-4)" }}>optional</span>}
      </div>
      <div style={{ position: "relative" }}>
        <input
          type={show ? "text" : "password"}
          placeholder={isSet ? "Saved — paste a new key to replace" : `Paste your ${label} key…`}
          value={isSet ? "" : value}
          onChange={e => onChange(e.target.value)}
          style={{
            width: "100%", boxSizing: "border-box", padding: "7px 36px 7px 10px",
            borderRadius: 6, border: "1px solid var(--border)",
            background: "var(--surface-2)", color: "var(--ink-1)",
            fontFamily: "monospace", fontSize: 12,
          }}
          autoComplete="off" spellCheck={false}
        />
        <button onClick={() => setShow(s => !s)}
          style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)",
                   background: "none", border: "none", cursor: "pointer", color: "var(--ink-4)", fontSize: 12, padding: 0 }}>
          {show ? "hide" : "show"}
        </button>
      </div>
      {isSet && <div style={{ fontSize: 11, marginTop: 3, color: "var(--rust)" }}>◆ Key is saved</div>}
      {hint && !isSet && <div style={{ fontSize: 11, marginTop: 3, color: "var(--ink-4)" }}>{hint}</div>}
    </div>
  );
}

function SettingsCategoryPicker({ label, hint, categories, selected, onToggle, search, onSearch }: {
  label: string; hint: string;
  categories: string[] | ArxivCat[];
  selected: string[];
  onToggle: (id: string) => void;
  search: string;
  onSearch: (v: string) => void;
}) {
  const isBiorxiv = typeof categories[0] === "string";
  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    if (!q) return categories;
    return (categories as Array<string | ArxivCat>).filter(c =>
      (typeof c === "string" ? c : `${c.id} ${c.label}`).toLowerCase().includes(q)
    );
  }, [categories, search]);

  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>
        {label}
        <span style={{ fontWeight: 400, color: "var(--ink-4)", marginLeft: 6 }}>{hint}</span>
      </div>
      {selected.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 6 }}>
          {selected.map(id => (
            <span key={id} className="im-tag im-cat-tag" style={{ fontSize: 11, fontFamily: "var(--font-mono)" }}>
              {id}<button onClick={() => onToggle(id)}>✕</button>
            </span>
          ))}
        </div>
      )}
      <input
        className="im-cat-search"
        placeholder={`Search ${label.toLowerCase()}…`}
        value={search}
        onChange={e => onSearch(e.target.value)}
        style={{ marginBottom: 6 }}
      />
      <div className="im-cat-grid" style={{ maxHeight: 120 }}>
        {(filtered as Array<string | ArxivCat>).map(c => {
          const id = isBiorxiv ? c as string : (c as ArxivCat).id;
          const group = isBiorxiv ? null : (c as ArxivCat).group;
          const on = selected.includes(id);
          return (
            <button key={id} className={"im-cat-pill" + (on ? " on" : "")} onClick={() => onToggle(id)}
                    title={isBiorxiv ? id : `${(c as ArxivCat).id} · ${(c as ArxivCat).label}`}>
              {group && <span className="im-cat-group">{group}</span>}
              <span className="im-cat-label">{group ? id.split(".").pop() || id : id}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

interface ApiConfig {
  llm_provider?: string; llm_model?: string; llm_base_url?: string;
  llm_enable_thinking?: boolean; llm_temperature?: number; llm_max_tokens?: number;
  agent_strip_parallel_tool_calls?: boolean;
  llm_top_p?: number | null; llm_top_k?: number | null;
  llm_repetition_penalty?: number | null; llm_presence_penalty?: number | null;
  ocr_provider?: string; ocr_model?: string; ocr_base_url?: string;
  ocr_max_tokens?: number; ocr_dpi?: number; ocr_semaphore_limit?: number; ocr_text_extract?: boolean;
  embed_provider?: string; embed_model?: string;
  embed_vllm_base_url?: string; embed_ollama_base_url?: string;
  prefs?: { max_results_per_source?: number; days_lookback?: number; arxiv_categories?: string[]; biorxiv_categories?: string[]; wikipedia_languages?: string[] };
  keys: Array<{ env_var: string; present: boolean; optional?: boolean }>;
}

export interface ApiConfigModalProps {
  open: boolean;
  onClose: () => void;
  onReload?: () => void;
}

export function ApiConfigModal({ open, onClose }: ApiConfigModalProps) {
  const [tab, setTab] = useState("ai");
  const [config, setConfig] = useState<ApiConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveOk, setSaveOk] = useState(false);

  const [anthropicKey, setAnthropicKey] = useState(MASKED);
  const [deepseekKey,  setDeepseekKey]  = useState(MASKED);
  const [geminiKey,    setGeminiKey]    = useState(MASKED);

  const KEY_STATE: Record<string, [string, (v: string) => void, string, string]> = {
    anthropic_api_key: [anthropicKey, setAnthropicKey, "ANTHROPIC_API_KEY", "Get yours at console.anthropic.com"],
    deepseek_api_key:  [deepseekKey,  setDeepseekKey,  "DEEPSEEK_API_KEY",  "Get yours at platform.deepseek.com"],
    gemini_api_key:    [geminiKey,    setGeminiKey,    "GEMINI_API_KEY",    "Get yours at aistudio.google.com"],
  };

  const [provider, setProvider] = useState("ollama");
  const [modelName, setModelName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [llmThinking, setLlmThinking] = useState(false);
  const [llmTemp, setLlmTemp] = useState(0.1);
  const [llmMaxTokens, setLlmMaxTokens] = useState(4096);
  const [llmTopP, setLlmTopP] = useState("");
  const [llmTopK, setLlmTopK] = useState("");
  const [llmRepPenalty, setLlmRepPenalty] = useState("");
  const [llmPresencePenalty, setLlmPresencePenalty] = useState("");
  const [stripParallelToolCalls, setStripParallelToolCalls] = useState(true);

  const [embedProvider, setEmbedProvider] = useState("vllm");
  const [embedModel, setEmbedModel] = useState("Qwen/Qwen3-Embedding-0.6B");
  const [embedVllmUrl, setEmbedVllmUrl] = useState("http://localhost:8888/v1");
  const [embedOllamaUrl, setEmbedOllamaUrl] = useState("http://localhost:11434");

  const [ocrTextExtract, setOcrTextExtract] = useState(false);
  const [ocrProvider, setOcrProvider] = useState("");
  const [ocrModel, setOcrModel] = useState("");
  const [ocrBaseUrl, setOcrBaseUrl] = useState("");
  const [ocrMaxTokens, setOcrMaxTokens] = useState(2048);
  const [ocrDpi, setOcrDpi] = useState(200);
  const [ocrSemaphore, setOcrSemaphore] = useState(1);

  const [maxResults, setMaxResults] = useState(100);
  const [daysLookback, setDaysLookback] = useState(7);
  const [arxivCats, setArxivCats] = useState<string[]>([]);
  const [arxivSearch, setArxivSearch] = useState("");
  const [biorxivCats, setBiorxivCats] = useState<string[]>([]);
  const [biorxivSearch, setBiorxivSearch] = useState("");
  const [wikiLangs, setWikiLangs] = useState(["en"]);

  const toggleArxivCat = (id: string) =>
    setArxivCats(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  const toggleBiorxivCat = (id: string) =>
    setBiorxivCats(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);

  const loadConfig = () => {
    setLoading(true);
    api.fetchConfig()
      .then(raw => { const c = raw as ApiConfig | null;
        if (!c) return;
        setConfig(c);
        setProvider(c.llm_provider || "ollama");
        setModelName(c.llm_model || "");
        setBaseUrl(c.llm_base_url || "");
        setLlmThinking(c.llm_enable_thinking ?? false);
        setStripParallelToolCalls(c.agent_strip_parallel_tool_calls ?? true);
        setLlmTemp(c.llm_temperature ?? 0.1);
        setLlmMaxTokens(c.llm_max_tokens ?? 4096);
        setLlmTopP(c.llm_top_p != null ? String(c.llm_top_p) : "");
        setLlmTopK(c.llm_top_k != null ? String(c.llm_top_k) : "");
        setLlmRepPenalty(c.llm_repetition_penalty != null ? String(c.llm_repetition_penalty) : "");
        setLlmPresencePenalty(c.llm_presence_penalty != null ? String(c.llm_presence_penalty) : "");
        setOcrProvider(c.ocr_provider || "");
        setOcrModel(c.ocr_model || "");
        setOcrBaseUrl(c.ocr_base_url || "");
        setOcrMaxTokens(c.ocr_max_tokens ?? 2048);
        setOcrDpi(c.ocr_dpi ?? 200);
        setOcrSemaphore(c.ocr_semaphore_limit ?? 1);
        setOcrTextExtract(c.ocr_text_extract ?? false);
        setEmbedProvider(c.embed_provider || "vllm");
        setEmbedModel(c.embed_model || "Qwen/Qwen3-Embedding-0.6B");
        setEmbedVllmUrl(c.embed_vllm_base_url || "http://localhost:8888/v1");
        setEmbedOllamaUrl(c.embed_ollama_base_url || "http://localhost:11434");
        if (c.prefs) {
          setMaxResults(c.prefs.max_results_per_source || 100);
          setDaysLookback(c.prefs.days_lookback || 7);
          setArxivCats(c.prefs.arxiv_categories || []);
          setBiorxivCats(c.prefs.biorxiv_categories || []);
          setWikiLangs(c.prefs.wikipedia_languages || ["en"]);
        }
        const findKey = (env: string) => c.keys.find(k => k.env_var === env);
        if (!findKey("ANTHROPIC_API_KEY")?.present) setAnthropicKey("");
        if (!findKey("DEEPSEEK_API_KEY")?.present)  setDeepseekKey("");
        if (!findKey("GEMINI_API_KEY")?.present)    setGeminiKey("");
      })
      .catch(() => setConfig(null))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (!open) return;
    setSaveOk(false);
    loadConfig();
  }, [open]);

  const save = async () => {
    setSaving(true);
    const body: Record<string, unknown> = {
      llm_provider: provider,
      llm_model: modelName || undefined,
      llm_base_url: baseUrl || undefined,
      llm_enable_thinking: llmThinking,
      agent_strip_parallel_tool_calls: stripParallelToolCalls,
      llm_temperature: llmTemp,
      llm_max_tokens: llmMaxTokens,
      llm_top_p: llmTopP !== "" ? Number(llmTopP) : undefined,
      llm_top_k: llmTopK !== "" ? Number(llmTopK) : undefined,
      llm_repetition_penalty: llmRepPenalty !== "" ? Number(llmRepPenalty) : undefined,
      llm_presence_penalty: llmPresencePenalty !== "" ? Number(llmPresencePenalty) : undefined,
      ocr_provider: ocrProvider || undefined,
      ocr_model: ocrModel || undefined,
      ocr_base_url: ocrBaseUrl || undefined,
      ocr_max_tokens: ocrMaxTokens,
      ocr_dpi: ocrDpi,
      ocr_semaphore_limit: ocrSemaphore,
      ocr_text_extract: ocrTextExtract,
      embed_provider: embedProvider,
      embed_model: embedModel || undefined,
      embed_vllm_base_url: embedVllmUrl || undefined,
      embed_ollama_base_url: embedOllamaUrl || undefined,
      max_results_per_source: maxResults,
      days_lookback: daysLookback,
      arxiv_categories: arxivCats,
      biorxiv_categories: biorxivCats,
      wikipedia_languages: wikiLangs,
    };
    if (anthropicKey !== MASKED && anthropicKey) body.anthropic_api_key = anthropicKey;
    if (deepseekKey  !== MASKED && deepseekKey)  body.deepseek_api_key  = deepseekKey;
    if (geminiKey    !== MASKED && geminiKey)     body.gemini_api_key    = geminiKey;

    const updated = await api.saveConfig(body).catch(() => null) as ApiConfig | null;
    setSaving(false);
    if (updated) {
      setConfig(updated);
      setSaveOk(true);
      const findKey = (env: string) => updated.keys.find(k => k.env_var === env);
      if (findKey("ANTHROPIC_API_KEY")?.present) setAnthropicKey(MASKED);
      if (findKey("DEEPSEEK_API_KEY")?.present)  setDeepseekKey(MASKED);
      if (findKey("GEMINI_API_KEY")?.present)    setGeminiKey(MASKED);
    }
  };

  if (!open) return null;

  const providerMeta = LLM_PROVIDERS.find(p => p.id === provider) || LLM_PROVIDERS[0];
  const missingRequired = config?.keys.filter(k => !k.optional && !k.present) || [];
  const isFirstRun = config && missingRequired.length > 0;

  const tabList: [string, string][] = [["ai","AI Model"], ["keys","API Keys"], ["ocr","OCR"], ["embed","Embeddings"], ["ingest","Ingestion"]];

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="ingest-modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 520, minHeight: "unset" }}>
        <div className="im-head">
          <div>
            <div className="im-kicker">SETTINGS</div>
            <div className="im-title">{isFirstRun ? "Welcome — let's get you set up" : "Settings"}</div>
          </div>
          <button className="im-close" onClick={onClose}>✕</button>
        </div>

        {isFirstRun && (
          <div style={{ margin: "0 0 4px", padding: "10px 16px", background: "rgba(162,62,34,0.08)", borderLeft: "3px solid var(--rust)", fontSize: 13, color: "var(--ink-2)", lineHeight: 1.5 }}>
            To start ingesting papers you need at least an LLM configured.
            Fill in the fields below and click <b>Save</b> — no terminal needed.
          </div>
        )}

        <div className="lib-tabrow" style={{ padding: "0", borderBottom: "1px solid var(--border)", marginBottom: 0 }}>
          {tabList.map(([k, lbl]) => (
            <button key={k} className={"lib-tab " + (tab === k ? "on" : "")} onClick={() => setTab(k)}>{lbl}</button>
          ))}
        </div>

        {tab === "ai" && (
          <div className="im-section" style={{ paddingTop: 16 }}>
            {loading && <div style={{ color: "var(--ink-4)", fontSize: 13 }}>Loading current config…</div>}
            {!loading && !config && <div style={{ color: "var(--ink-4)", fontSize: 13 }}>Cannot reach the backend. Make sure the server is running.</div>}
            {!loading && config && (<>
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8, color: "var(--ink-1)" }}>
                  Provider <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>— used for paper summaries and chat</span>
                </div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {LLM_PROVIDERS.map(p => (
                    <button key={p.id}
                      onClick={() => { setProvider(p.id); if (p.urlDefault && !baseUrl) setBaseUrl(p.urlDefault); }}
                      style={{ padding: "6px 12px", borderRadius: 6, fontSize: 12, cursor: "pointer", border: provider === p.id ? "1.5px solid var(--rust)" : "1px solid var(--border)", background: provider === p.id ? "rgba(162,62,34,0.08)" : "var(--surface-2)", color: "var(--ink-1)" }}>
                      <div style={{ fontWeight: 600 }}>{p.label}</div>
                      <div style={{ color: "var(--ink-4)", fontSize: 11, marginTop: 1 }}>{p.hint}</div>
                    </button>
                  ))}
                </div>
              </div>

              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>Model name</div>
                <input
                  placeholder={provider === "ollama" ? "e.g. llama3.1:70b" : provider === "vllm" ? "e.g. meta-llama/Llama-3.1-70B-Instruct" : provider === "anthropic" ? "e.g. claude-sonnet-4-6" : provider === "deepseek" ? "e.g. deepseek-v4-flash" : provider === "gemini" ? "e.g. gemini-2.5-flash" : "e.g. model-name"}
                  value={modelName} onChange={e => setModelName(e.target.value)}
                  style={{ width: "100%", boxSizing: "border-box", padding: "7px 10px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--surface-2)", color: "var(--ink-1)", fontFamily: "monospace", fontSize: 12 }}
                />
              </div>

              {providerMeta.needsUrl && (
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>{providerMeta.urlLabel}</div>
                  <input value={baseUrl} onChange={e => setBaseUrl(e.target.value)} placeholder={providerMeta.urlDefault}
                    style={{ width: "100%", boxSizing: "border-box", padding: "7px 10px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--surface-2)", color: "var(--ink-1)", fontFamily: "monospace", fontSize: 12 }} />
                  <div style={{ fontSize: 11, marginTop: 3, color: "var(--ink-4)" }}>
                    {provider === "ollama" ? "Ollama must be running locally. Pull a model first: ollama pull llama3.1:70b" : "vLLM server endpoint (OpenAI-compatible)"}
                  </div>
                </div>
              )}

              <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "4px 0 14px" }} />
              <div style={{ fontSize: 12, fontWeight: 500, color: "var(--ink-3)", marginBottom: 10 }}>
                Generation parameters <span style={{ fontWeight: 400, color: "var(--ink-4)", marginLeft: 6 }}>applied to every summary and chat request</span>
              </div>

              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>Temperature <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>randomness · 0 = deterministic</span></div>
                <ChipInput value={llmTemp} onChange={setLlmTemp} presets={[0, 0.1, 0.3, 0.7, 1.0]} min={0} max={2} step={0.05} format={v => v.toFixed(2)} />
              </div>
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>Max tokens</div>
                <ChipInput value={llmMaxTokens} onChange={setLlmMaxTokens} presets={[512, 1024, 2048, 4096, 8192]} min={256} max={16384} step={256} />
              </div>

              {(provider === "ollama" || provider === "vllm" || provider === "openai") && (
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>Top P <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>nucleus sampling · leave blank for default</span></div>
                  <ChipInput value={llmTopP !== "" ? Number(llmTopP) : 0.9} onChange={v => setLlmTopP(String(v))} presets={[0.7, 0.85, 0.9, 0.95, 1.0]} min={0.01} max={1} step={0.01} format={v => v.toFixed(2)} />
                </div>
              )}
              {(provider === "ollama" || provider === "vllm") && (
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>Top K <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>limits candidate pool · leave blank for default</span></div>
                  <input type="number" min="1" step="1" value={llmTopK} onChange={e => setLlmTopK(e.target.value)} placeholder="e.g. 40"
                    style={{ width: 110, padding: "7px 10px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--surface-2)", color: "var(--ink-1)", fontFamily: "monospace", fontSize: 12 }} />
                </div>
              )}
              {provider === "ollama" && (
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>Repetition penalty <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>1.0 = off · leave blank for default</span></div>
                  <input type="number" min="0.5" max="2" step="0.05" value={llmRepPenalty} onChange={e => setLlmRepPenalty(e.target.value)} placeholder="e.g. 1.1"
                    style={{ width: 110, padding: "7px 10px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--surface-2)", color: "var(--ink-1)", fontFamily: "monospace", fontSize: 12 }} />
                </div>
              )}
              {(provider === "vllm" || provider === "openai") && (
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>Presence penalty <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>encourages new topics · leave blank for default</span></div>
                  <input type="number" min="-2" max="2" step="0.1" value={llmPresencePenalty} onChange={e => setLlmPresencePenalty(e.target.value)} placeholder="e.g. 0.5"
                    style={{ width: 110, padding: "7px 10px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--surface-2)", color: "var(--ink-1)", fontFamily: "monospace", fontSize: 12 }} />
                </div>
              )}
              {(provider === "ollama" || provider === "anthropic") && (
                <div style={{ marginBottom: 14 }}>
                  <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                    <input type="checkbox" checked={llmThinking} onChange={e => setLlmThinking(e.target.checked)} />
                    <span style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-1)" }}>Enable thinking / reasoning</span>
                  </label>
                  <div style={{ fontSize: 11, marginTop: 4, color: "var(--ink-4)", paddingLeft: 22 }}>
                    {provider === "anthropic" ? "Anthropic extended thinking — forces temperature to 1.0. Uses ~50% of max tokens for reasoning." : "Ollama think=true — passes options.think to the model."}
                  </div>
                </div>
              )}

              <div style={{ borderTop: "1px solid var(--rule)", margin: "12px 0 14px", paddingTop: 14 }}>
                <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.07em", color: "var(--ink-3)", marginBottom: 10 }}>AGENT</div>
                <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                  <input type="checkbox" checked={stripParallelToolCalls} onChange={e => setStripParallelToolCalls(e.target.checked)} />
                  <span style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-1)" }}>Strip parallel tool calls</span>
                </label>
                <div style={{ fontSize: 11, marginTop: 4, color: "var(--ink-4)", paddingLeft: 22 }}>
                  Keep only the first tool call per turn. Fixes providers that ignore <code>parallel_tool_calls=false</code> (e.g. DeepSeek V4) and return multiple calls at once, causing a 400 on the follow-up. Disable if your provider handles parallel calls correctly.
                </div>
              </div>

              {saveOk && <div style={{ padding: "8px 12px", background: "rgba(162,62,34,0.08)", borderRadius: 6, fontSize: 12, color: "var(--rust)", marginBottom: 8 }}>◆ Saved — settings written to .env and reloaded</div>}
            </>)}
          </div>
        )}

        {tab === "keys" && (
          <div className="im-section" style={{ paddingTop: 16 }}>
            {loading && <div style={{ color: "var(--ink-4)", fontSize: 13 }}>Loading current config…</div>}
            {!loading && !config && <div style={{ color: "var(--ink-4)", fontSize: 13 }}>Cannot reach the backend.</div>}
            {!loading && config && (<>
              {providerMeta.needsKey && providerMeta.keyField && (() => {
                const entry = KEY_STATE[providerMeta.keyField];
                if (!entry) return null;
                const [kVal, kSet, , kHint] = entry;
                return <KeyField label={providerMeta.label + " API key"} value={kVal} onChange={kSet} hint={kHint} />;
              })()}
              {!providerMeta.needsKey && (
                <div style={{ fontSize: 13, color: "var(--ink-4)", padding: "12px 0" }}>
                  The selected provider ({providerMeta.label}) runs locally and doesn't require an API key.
                </div>
              )}
              {saveOk && <div style={{ padding: "8px 12px", background: "rgba(162,62,34,0.08)", borderRadius: 6, fontSize: 12, color: "var(--rust)", marginBottom: 8 }}>◆ Saved — settings written to .env and reloaded</div>}
            </>)}
          </div>
        )}

        {tab === "ocr" && (
          <div className="im-section" style={{ paddingTop: 16 }}>
            {!config && <div style={{ color: "var(--ink-4)", fontSize: 13 }}>Cannot reach backend.</div>}
            {config && (<>
              <div style={{ marginBottom: 18, padding: "12px 14px", background: "var(--bg-2)", borderRadius: 6, border: "1px solid var(--border)" }}>
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 10, color: "var(--ink-1)" }}>Text extraction method</div>
                {[
                  { val: false, label: "Vision OCR (AI-powered)", sub: "Renders PDF pages as images and uses a multimodal LLM to extract text." },
                  { val: true,  label: "PDF text extract (fast)",  sub: "Reads embedded text directly from the PDF with PyMuPDF — no GPU or LLM needed." },
                ].map(opt => (
                  <div key={String(opt.val)} onClick={() => setOcrTextExtract(opt.val)} style={{ display: "flex", alignItems: "flex-start", gap: 10, marginBottom: 8, cursor: "pointer" }}>
                    <div style={{ width: 16, height: 16, marginTop: 1, borderRadius: "50%", flexShrink: 0, border: ocrTextExtract === opt.val ? "5px solid var(--rust)" : "1.5px solid var(--border)", background: "var(--surface-2)" }} />
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-1)" }}>{opt.label}</div>
                      <div style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 2, lineHeight: 1.5 }}>{opt.sub}</div>
                    </div>
                  </div>
                ))}
              </div>
              <div style={{ opacity: ocrTextExtract ? 0.45 : 1, pointerEvents: ocrTextExtract ? "none" : "auto" }}>
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6, color: "var(--ink-1)" }}>OCR provider</div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {OCR_PROVIDERS.map(p => (
                      <button key={p.id} onClick={() => setOcrProvider(p.id)}
                        style={{ padding: "6px 12px", borderRadius: 6, fontSize: 12, cursor: "pointer", border: ocrProvider === p.id ? "1.5px solid var(--rust)" : "1px solid var(--border)", background: ocrProvider === p.id ? "rgba(162,62,34,0.08)" : "var(--surface-2)", color: "var(--ink-1)" }}>
                        <div style={{ fontWeight: 600 }}>{p.label}</div>
                        <div style={{ color: "var(--ink-4)", fontSize: 11, marginTop: 1 }}>{p.hint}</div>
                      </button>
                    ))}
                  </div>
                </div>
                {ocrProvider && (<>
                  <div style={{ marginBottom: 14 }}>
                    <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>OCR model name</div>
                    <input placeholder={ocrProvider === "ollama" ? "e.g. qwen2.5vl:7b" : "e.g. nanonets/Nanonets-OCR2-3B"} value={ocrModel} onChange={e => setOcrModel(e.target.value)}
                      style={{ width: "100%", boxSizing: "border-box", padding: "7px 10px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--surface-2)", color: "var(--ink-1)", fontFamily: "monospace", fontSize: 12 }} />
                  </div>
                  <div style={{ marginBottom: 14 }}>
                    <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>{ocrProvider === "ollama" ? "Ollama URL" : "vLLM URL"}</div>
                    <input value={ocrBaseUrl} onChange={e => setOcrBaseUrl(e.target.value)} placeholder={ocrProvider === "ollama" ? "http://localhost:11434" : "http://localhost:8000/v1"}
                      style={{ width: "100%", boxSizing: "border-box", padding: "7px 10px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--surface-2)", color: "var(--ink-1)", fontFamily: "monospace", fontSize: 12 }} />
                  </div>
                </>)}
                <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "4px 0 14px" }} />
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>Max tokens per page</div>
                  <ChipInput value={ocrMaxTokens} onChange={setOcrMaxTokens} presets={[512, 1024, 2048, 4096]} min={256} max={8192} step={256} />
                </div>
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>Render DPI <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>higher = better quality, larger images</span></div>
                  <ChipInput value={ocrDpi} onChange={setOcrDpi} presets={[100, 150, 200, 300, 400]} min={72} max={400} step={25} format={v => v + " dpi"} />
                </div>
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>Concurrency <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>max parallel page requests</span></div>
                  <ChipInput value={ocrSemaphore} onChange={setOcrSemaphore} presets={[1, 2, 3, 4, 6, 8]} min={1} max={8} step={1} />
                </div>
              </div>
            </>)}
          </div>
        )}

        {tab === "embed" && (
          <div className="im-section" style={{ paddingTop: 16 }}>
            <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 18, lineHeight: 1.6 }}>
              Papers are embedded into <b>ChromaDB</b> for semantic similarity search.
            </div>
            <div className="im-label"><span>Provider</span></div>
            <div style={{ display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
              {[
                { val: "vllm",    label: "vLLM",    sub: "GPU-accelerated · OpenAI-compat /v1/embeddings" },
                { val: "ollama",  label: "Ollama",  sub: "Local CPU/GPU · no extra deps" },
                { val: "default", label: "Default", sub: "ChromaDB built-in ONNX · no extra deps" },
              ].map(opt => (
                <div key={opt.val} className={"watch-item" + (embedProvider === opt.val ? " on" : "")} style={{ flex: "1 1 45%", cursor: "pointer" }}
                  onClick={() => { setEmbedProvider(opt.val); if (opt.val === "vllm" && !embedModel) setEmbedModel("Qwen/Qwen3-Embedding-0.6B"); if (opt.val === "ollama" && !embedModel) setEmbedModel("nomic-embed-text"); }}>
                  <span className={"watch-toggle " + (embedProvider === opt.val ? "on" : "")}><span /></span>
                  <div className="watch-main">
                    <div className="watch-name">{opt.label}</div>
                    <div className="watch-sub">{opt.sub}</div>
                  </div>
                </div>
              ))}
            </div>
            {embedProvider !== "default" && (<>
              <div className="im-label"><span>Model</span></div>
              <input style={{ width: "100%", padding: "7px 10px", fontSize: 12, fontFamily: "var(--mono)", background: "var(--bg)", border: "1px solid var(--rule)", borderRadius: 4, color: "var(--ink-1)", marginBottom: 14, boxSizing: "border-box" }}
                value={embedModel} onChange={e => setEmbedModel(e.target.value)} placeholder={embedProvider === "vllm" ? "Qwen/Qwen3-Embedding-0.6B" : "nomic-embed-text"} />
            </>)}
            {embedProvider === "vllm" && (<>
              <div className="im-label"><span>vLLM base URL</span></div>
              <input style={{ width: "100%", padding: "7px 10px", fontSize: 12, fontFamily: "var(--mono)", background: "var(--bg)", border: "1px solid var(--rule)", borderRadius: 4, color: "var(--ink-1)", marginBottom: 14, boxSizing: "border-box" }}
                value={embedVllmUrl} onChange={e => setEmbedVllmUrl(e.target.value)} placeholder="http://localhost:8888/v1" />
            </>)}
            {embedProvider === "ollama" && (<>
              <div className="im-label"><span>Ollama base URL</span></div>
              <input style={{ width: "100%", padding: "7px 10px", fontSize: 12, fontFamily: "var(--mono)", background: "var(--bg)", border: "1px solid var(--rule)", borderRadius: 4, color: "var(--ink-1)", marginBottom: 14, boxSizing: "border-box" }}
                value={embedOllamaUrl} onChange={e => setEmbedOllamaUrl(e.target.value)} placeholder="http://localhost:11434" />
            </>)}
          </div>
        )}

        {tab === "ingest" && (
          <div className="im-section" style={{ paddingTop: 16 }}>
            {!config && <div style={{ color: "var(--ink-4)", fontSize: 13 }}>Cannot reach backend.</div>}
            {config && (<>
              <div style={{ marginBottom: 18 }}>
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>Papers per source <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>per ingest run, per source</span></div>
                <ChipInput value={maxResults} onChange={setMaxResults} presets={[25, 50, 100, 200, 500]} min={25} max={500} step={25} />
              </div>
              <div style={{ marginBottom: 18 }}>
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>Default look-back window <span style={{ fontWeight: 400, color: "var(--ink-4)" }}>days of papers to consider</span></div>
                <ChipInput value={daysLookback} onChange={setDaysLookback} presets={[1, 3, 7, 14, 30, 60, 90]} min={1} max={90} step={1} format={v => v === 1 ? "1 day" : v + " days"} />
              </div>
              <SettingsCategoryPicker label="arXiv categories" hint="default filter when no interests are typed" categories={SETTINGS_ARXIV_CATEGORIES} selected={arxivCats} onToggle={toggleArxivCat} search={arxivSearch} onSearch={setArxivSearch} />
              <SettingsCategoryPicker label="bioRxiv categories" hint="default filter for bioRxiv pulls" categories={SETTINGS_BIORXIV_CATEGORIES} selected={biorxivCats} onToggle={toggleBiorxivCat} search={biorxivSearch} onSearch={setBiorxivSearch} />
              <div style={{ marginBottom: 18 }}>
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4, color: "var(--ink-1)" }}>Wikipedia languages</div>
                <div className="im-cat-grid" style={{ gap: 6, marginTop: 6 }}>
                  {["en","de","fr","es","it","pt","nl","pl","ru","ja","zh","ar","ko","sv","fi"].map(lang => (
                    <button key={lang} className={"im-cat-pill" + (wikiLangs.includes(lang) ? " on" : "")} onClick={() => setWikiLangs(prev => prev.includes(lang) ? (prev.length > 1 ? prev.filter(x => x !== lang) : prev) : [...prev, lang])} title={lang}>
                      <span className="im-cat-label">{lang}</span>
                    </button>
                  ))}
                </div>
              </div>
            </>)}
          </div>
        )}

        <div className="im-foot">
          <div style={{ fontSize: 12, color: "var(--ink-4)" }}>
            Keys are stored in <code style={{fontSize:11}}>.env</code> on the server — never sent elsewhere.
          </div>
          <div className="im-foot-actions">
            <button className="ghost" onClick={onClose}>Close</button>
            <button className="primary" onClick={() => void save()} disabled={saving || !config}>{saving ? "Saving…" : "Save settings"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
