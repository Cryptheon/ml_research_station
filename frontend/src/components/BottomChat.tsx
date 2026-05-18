import { useState, useRef, useEffect, useCallback } from "react";
import { api } from "../api";
import type { Paper } from "../types";
import type { ToolEntry } from "./ToolPills";
import { AgentMsg } from "./ToolPills";

const CHAT_WELCOME: Record<string, Msg> = {
  general: { role: "agent", text: "General LLM mode — no Meridian context, no tools. Just a direct conversation." },
  paper:   { role: "agent", text: "Paper chat — I can see the active paper, its summary, and extracted text. Ask me anything about it." },
  agent:   { role: "agent", text: "Agent mode — I have access to all Meridian tools: search, summarise, OCR, graph traversal, dashboards, and more." },
};

const CHAT_SUGG = [
  "Summarise this paper",
  "Contradict this",
  "Velocity vs priors",
  "Related · 30d",
  "Cite-by graph",
];

interface Msg {
  role: string;
  text?: string;
  thinking?: boolean | string;
  tools?: ToolEntry[];
  streaming?: boolean;
  images?: string[];
}

interface AttachedImage {
  dataUrl: string;
  name: string;
}

interface ToolMapEntry extends ToolEntry {
  children: ToolMapEntry[];
}

export interface BottomChatProps {
  open: boolean;
  onClose: () => void;
  height: number;
  setHeight: (h: number) => void;
  paper: Paper | null;
  papers: Paper[];
  onOpenPaper: (id: string) => void;
}

export function BottomChat({ open, onClose, height, setHeight, paper, papers: _papers, onOpenPaper }: BottomChatProps) {
  const [msgs, setMsgs] = useState<Msg[]>(() => {
    const saved = localStorage.getItem("mpe:chat-mode");
    const legacy = localStorage.getItem("mpe:agent-mode");
    const mode = saved || (legacy === "1" ? "agent" : "paper");
    return [CHAT_WELCOME[mode]];
  });
  const [input, setInput] = useState("");
  const [attachedImages, setAttachedImages] = useState<AttachedImage[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [chatMode, setChatMode] = useState<string>(() => {
    const legacy = localStorage.getItem("mpe:agent-mode");
    const saved = localStorage.getItem("mpe:chat-mode");
    if (saved) return saved;
    if (legacy === "1") return "agent";
    return "paper";
  });
  const [thinkingEnabled, setThinkingEnabled] = useState(() => localStorage.getItem("mpe:thinking") !== "0");
  const [paperDetached, setPaperDetached] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const chatMapRef = useRef<Record<string, unknown>>({});
  const currentKeyRef = useRef("__global__");
  const abortRef = useRef<{ abort: () => void } | null>(null);

  const activePaper = paperDetached ? null : paper;
  const chatKey = useCallback(() => activePaper?.id || "__global__", [activePaper?.id]);

  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distFromBottom < 120) el.scrollTop = el.scrollHeight;
  }, [msgs]);

  const setMode = (mode: string) => {
    setChatMode(mode);
    localStorage.setItem("mpe:chat-mode", mode);
  };

  const toggleThinking = () => {
    setThinkingEnabled(v => {
      const next = !v;
      localStorage.setItem("mpe:thinking", next ? "1" : "0");
      return next;
    });
  };

  const loadChatById = useCallback((chatId: string) => {
    setLoading(true);
    api.fetchChatMessages(chatId).then(raw => { const messages = raw as Msg[];
      if (messages && messages.length > 0) {
        setMsgs(messages.map(m => ({
          role: m.role,
          text: m.text,
          thinking: m.thinking,
          images: m.images && m.images.length > 0 ? m.images : undefined,
          tools: m.tools && m.tools.length > 0 ? m.tools : undefined,
        })));
      } else {
        setMsgs([CHAT_WELCOME[chatMode]]);
      }
    }).catch(() => setMsgs([CHAT_WELCOME[chatMode]])).finally(() => setLoading(false));
  }, [chatMode]);

  useEffect(() => {
    const handler = (ev: Event) => {
      const detail = (ev as CustomEvent<{ chatId?: unknown }>).detail || {};
      const key = chatKey();
      if (detail.chatId) {
        chatMapRef.current[key] = detail.chatId;
        currentKeyRef.current = key;
        loadChatById(String(detail.chatId));
      } else {
        delete chatMapRef.current[key];
        currentKeyRef.current = key;
        setMsgs([CHAT_WELCOME[chatMode]]);
      }
    };
    document.addEventListener("rs:open-chat", handler);
    return () => document.removeEventListener("rs:open-chat", handler);
  }, [paper?.id, chatMode, chatKey, loadChatById]);

  useEffect(() => {
    if (!open) return;
    const key = chatKey();
    if (currentKeyRef.current === key && chatMapRef.current[key]) return;
    currentKeyRef.current = key;
    if (abortRef.current) return;

    setLoading(true);
    const paperId = activePaper?.id || null;
    api.fetchPaperChats(paperId).then(rawChats => {
      const chats = rawChats as Array<{ id: string }>;
      if (chats && chats.length > 0) {
        const latest = chats[0];
        chatMapRef.current[key] = latest.id;
        return api.fetchChatMessages(latest.id);
      }
      return [] as unknown[];
    }).then(raw => { const messages = raw as Msg[];
      if (messages && messages.length > 0) {
        setMsgs(messages.map(m => ({
          role: m.role,
          text: m.text,
          thinking: m.thinking,
          images: m.images && m.images.length > 0 ? m.images : undefined,
          tools: m.tools && m.tools.length > 0 ? m.tools : undefined,
        })));
      } else {
        setMsgs([CHAT_WELCOME[chatMode]]);
      }
    }).catch(() => setMsgs([CHAT_WELCOME[chatMode]])).finally(() => setLoading(false));
  }, [paper?.id, paperDetached, open]);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: MouseEvent) => {
      const vh = window.innerHeight;
      const h = Math.max(200, Math.min(vh * 0.75, vh - e.clientY));
      setHeight(h);
    };
    const onUp = () => setDragging(false);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [dragging, setHeight]);

  const ensureChatId = async () => {
    const key = chatKey();
    if (chatMapRef.current[key]) return chatMapRef.current[key];
    const chat = await api.newChat(activePaper?.id || null) as { id: unknown };
    chatMapRef.current[key] = chat.id;
    return chat.id;
  };

  const newConversation = async () => {
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null; }
    const key = chatKey();
    delete chatMapRef.current[key];
    setMsgs([CHAT_WELCOME[chatMode]]);
  };

  const sendRegular = async (text: string) => {
    const imgs = attachedImages.map(a => a.dataUrl);
    setMsgs(m => [...m, { role: "user", text, images: imgs }, { role: "agent", thinking: true, text: "", streaming: true }]);
    setInput("");
    setAttachedImages([]);

    let chatId: string | null;
    try { chatId = String(await ensureChatId()); }
    catch { chatId = null; }
    if (!chatId) {
      setMsgs(m => [...m.slice(0, -1), { role: "agent", text: "Could not create chat session.", streaming: false }]);
      return;
    }

    let thinkingAcc = "";
    let contentAcc = "";

    abortRef.current = api.streamChatMessage(chatId, text, activePaper?.id, chatMode, {
      onThinking: (delta: string) => {
        thinkingAcc += delta;
        setMsgs(m => [...m.slice(0, -1), { ...m[m.length - 1], thinking: thinkingAcc, streaming: true }]);
      },
      onContent: (delta: string) => {
        contentAcc += delta;
        setMsgs(m => {
          const last = m[m.length - 1];
          return [...m.slice(0, -1), { ...last, thinking: thinkingAcc || last.thinking, text: contentAcc, streaming: true }];
        });
      },
      onDone: () => {
        abortRef.current = null;
        setMsgs(m => {
          const last = { ...m[m.length - 1], streaming: false };
          if (!contentAcc && thinkingAcc) {
            const paras = thinkingAcc.split("\n\n").filter(p => p.trim());
            last.text = paras[paras.length - 1] || thinkingAcc;
          } else if (!contentAcc) {
            last.text = "No response.";
          }
          return [...m.slice(0, -1), last];
        });
        document.dispatchEvent(new CustomEvent("rs:chat-saved"));
      },
      onError: (err: string) => {
        abortRef.current = null;
        setMsgs(m => [...m.slice(0, -1), { role: "agent", text: `Error: ${err}`, streaming: false }]);
      },
    }, imgs);
  };

  const sendAgent = async (text: string) => {
    const imgs = attachedImages.map(a => a.dataUrl);
    setMsgs(m => [...m,
      { role: "user", text, images: imgs },
      { role: "agent", text: "", thinking: "", tools: [], streaming: true },
    ]);
    setInput("");
    setAttachedImages([]);

    let chatId: string | null;
    try { chatId = String(await ensureChatId()); }
    catch { chatId = null; }
    if (!chatId) {
      setMsgs(m => [...m.slice(0, -1), { role: "agent", text: "Could not create chat session.", streaming: false }]);
      return;
    }

    let thinkingAcc = "";
    let contentAcc = "";
    let errorFired = false;
    const toolsMap: Record<string, ToolMapEntry> = {};
    const childMap: Record<string, string> = {};
    const SUB_AGENTS = new Set(["research_expert", "processing_expert", "knowledge_expert", "analysis_expert"]);

    const updateLast = (patch: Partial<Msg>) => {
      setMsgs(m => {
        const last = { ...m[m.length - 1], ...patch };
        return [...m.slice(0, -1), last];
      });
    };

    abortRef.current = api.streamAgentMessage(chatId, text, activePaper?.id, thinkingEnabled, {
      onThinking: (delta: string) => {
        thinkingAcc += delta;
        updateLast({ thinking: thinkingAcc });
      },
      onContent: (delta: string) => {
        contentAcc += delta;
        updateLast({ text: contentAcc });
      },
      onToolCall: ({ id, tool, input: rawInput, agent }: { id: string; tool: string; input: unknown; agent: string }) => {
        const input = rawInput as Record<string, unknown> | null;
        const isOrchestrator = !agent || agent === "Meridian";
        if (isOrchestrator && SUB_AGENTS.has(tool)) {
          toolsMap[id] = { id, tool, input, result: undefined, streaming: true, type: "sub_agent", children: [] };
        } else if (!isOrchestrator) {
          const allSA = Object.values(toolsMap).filter(e => e.type === "sub_agent");
          const openSA = [...allSA].reverse().find(e => e.streaming) || allSA[allSA.length - 1];
          if (openSA) {
            childMap[id] = openSA.id;
            openSA.children = [...openSA.children, { id, tool, input, result: undefined, streaming: true, children: [] }];
          }
        } else {
          toolsMap[id] = { id, tool, input, result: undefined, streaming: true, children: [] };
        }
        updateLast({ tools: Object.values(toolsMap) });
      },
      onToolResult: ({ id, tool, content }: { id: string; tool: string; content: string; agent?: string }) => {
        const parentId = childMap[id];
        if (parentId && toolsMap[parentId]) {
          toolsMap[parentId].children = toolsMap[parentId].children.map(c =>
            c.id === id ? { ...c, result: content, streaming: false } : c
          );
        } else if (toolsMap[id]) {
          toolsMap[id] = { ...toolsMap[id], result: content, streaming: false };
        }
        updateLast({ tools: Object.values(toolsMap) });
        if (content) {
          const m = content.match(/https?:\/\/[^\s"'<>]+\/workspace\/[^\s"'<>]+\.html/);
          if (m) document.dispatchEvent(new CustomEvent("rs:dashboard-created", { detail: { url: m[0] } }));
          if (tool === "add_note") {
            const entry = toolsMap[id] || Object.values(toolsMap).flatMap(e => e.children || []).find(c => c.id === id);
            const paperId = (entry?.input as Record<string, string> | undefined)?.paper_id;
            window.dispatchEvent(new CustomEvent("rs:note-added", { detail: { paperId } }));
          }
          if (tool === "graph_traverse") {
            window.dispatchEvent(new CustomEvent("rs:traversal-updated"));
          }
        }
      },
      onDone: () => {
        abortRef.current = null;
        setMsgs(m => {
          const last = { ...m[m.length - 1], streaming: false };
          if (!errorFired && !contentAcc && !Object.keys(toolsMap).length) last.text = "No response.";
          return [...m.slice(0, -1), last];
        });
        document.dispatchEvent(new CustomEvent("rs:chat-saved"));
        if (Object.keys(toolsMap).length > 0) {
          setTimeout(() => window.dispatchEvent(new CustomEvent("rs:refreshPapers")), 500);
        }
      },
      onError: (err: string) => {
        errorFired = true;
        abortRef.current = null;
        setMsgs(m => [...m.slice(0, -1), { role: "agent", text: `Error: ${err}`, streaming: false }]);
      },
    }, imgs);
  };

  const send = (text: string) => {
    if (!text.trim() && !attachedImages.length) return;
    if (chatMode === "agent") void sendAgent(text);
    else void sendRegular(text);
  };

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    files.forEach(file => {
      const reader = new FileReader();
      reader.onload = (ev) => {
        setAttachedImages(imgs => [...imgs, { dataUrl: ev.target!.result as string, name: file.name }]);
      };
      reader.readAsDataURL(file);
    });
  };

  const removeImage = (idx: number) => setAttachedImages(imgs => imgs.filter((_, i) => i !== idx));

  const onPaste = (e: React.ClipboardEvent) => {
    const items = Array.from(e.clipboardData?.items || []);
    const imageItems = items.filter(item => item.type.startsWith("image/"));
    if (!imageItems.length) return;
    e.preventDefault();
    imageItems.forEach(item => {
      const file = item.getAsFile();
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (ev) => {
        setAttachedImages(imgs => [...imgs, { dataUrl: ev.target!.result as string, name: "pasted-image.png" }]);
      };
      reader.readAsDataURL(file);
    });
  };

  if (!open) return null;

  return (
    <div className="bchat" style={{ height }}>
      {dragging && <div style={{ position: "fixed", inset: 0, zIndex: 9999, cursor: "ns-resize" }} />}
      <div className="bchat-drag" onMouseDown={() => setDragging(true)} />
      <div className="bchat-head">
        <div className="bchat-tabs">
          <button className="bchat-tab on">
            <span className="bchat-tab-dot" /> Agent
          </button>
        </div>
        <div className="bchat-scope">
          {paper && !paperDetached ? (
            <>
              <span className="bchat-scope-label">scope</span>
              <span className="bchat-scope-chip">@paper · {paper.id}</span>
            </>
          ) : paperDetached ? (
            <>
              <span className="bchat-scope-label">scope</span>
              <span className="bchat-scope-chip" style={{ opacity: 0.45, textDecoration: "line-through" }}>@paper</span>
            </>
          ) : <span className="bchat-scope-label">no paper selected</span>}
        </div>
        <div className="bchat-actions">
          <div className="chat-mode-seg" role="group" aria-label="Chat mode">
            <button className={"seg-btn" + (chatMode === "general" ? " active" : "")} onClick={() => setMode("general")} title="General LLM — no Meridian context">General</button>
            <button className={"seg-btn" + (chatMode === "paper"   ? " active" : "")} onClick={() => setMode("paper")}   title="Paper chat — LLM with paper context">Paper</button>
            <button className={"seg-btn" + (chatMode === "agent"   ? " active" : "")} onClick={() => setMode("agent")}   title="Agent — full tool access">⚡ Agent</button>
          </div>
          {chatMode === "agent" && (
            <button
              className={"ghost agent-toggle" + (thinkingEnabled ? " active" : "")}
              onClick={toggleThinking}
              title={thinkingEnabled ? "Thinking ON — click to disable" : "Thinking OFF — click to enable"}
              style={{ fontSize: 12 }}
            >
              ◈ {thinkingEnabled ? "Think" : "No think"}
            </button>
          )}
          {paper && (
            <button
              className={"ghost" + (paperDetached ? " active" : "")}
              onClick={() => setPaperDetached(v => !v)}
              title={paperDetached ? "Re-attach paper — restore paper context" : "Detach paper — agent runs without paper context"}
              style={{ fontSize: 12 }}
            >
              {paperDetached ? "⊙ Detached" : "⊗ Paper"}
            </button>
          )}
          <button className="ghost" onClick={() => void newConversation()} title="New conversation">+ New</button>
          <button className="ghost" onClick={onClose} title="Close (⌘`)">✕</button>
        </div>
      </div>
      <div className="bchat-body" ref={bodyRef}>
        {loading
          ? <div style={{ padding: "24px 20px", color: "var(--ink-4)", fontSize: 13 }}>Loading history…</div>
          : msgs.map((m, i) => (
              <AgentMsg
                key={i}
                m={m as Parameters<typeof AgentMsg>[0]["m"]}
                onOpen={(id) => { onOpenPaper(id); }}
                onStop={m.streaming && abortRef.current ? () => {
                  abortRef.current!.abort();
                  abortRef.current = null;
                  setMsgs(ms => {
                    const last = { ...ms[ms.length - 1], streaming: false };
                    if (!last.text) last.text = "(stopped)";
                    return [...ms.slice(0, -1), last];
                  });
                  setLoading(false);
                } : undefined}
              />
            ))
        }
      </div>
      <div className="bchat-foot">
        <div className="bchat-sugg">
          {CHAT_SUGG.map(s => (
            <button key={s} className="sugg" onClick={() => send(s)}>{s}</button>
          ))}
        </div>
        {attachedImages.length > 0 && (
          <div className="bchat-img-strip">
            {attachedImages.map((img, i) => (
              <div key={i} className="bchat-img-thumb">
                <img src={img.dataUrl} alt={img.name} />
                <button className="bchat-img-remove" onClick={() => removeImage(i)}>×</button>
              </div>
            ))}
          </div>
        )}
        <div className="bchat-input-row">
          <span className="bchat-prompt">›</span>
          <input
            placeholder={
              chatMode === "general" ? "Ask anything — general knowledge, math, code, writing" :
              chatMode === "paper"   ? "Ask about the paper — its methods, results, contributions, limitations" :
                                      "Ask the agent — search, summarise, compare papers, build dashboards"
            }
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") send(input); }}
            onPaste={onPaste}
            autoFocus
          />
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            style={{ display: "none" }}
            onChange={onFileChange}
          />
          <button
            className="ghost"
            onClick={() => fileInputRef.current?.click()}
            title="Attach image"
            style={{ fontSize: 16, padding: "0 6px" }}
          >⊕</button>
          <span className="bchat-routing">{
            chatMode === "agent" ? "⚡ agent" :
            chatMode === "general" ? "general" :
            "paper"
          }</span>
          <button className="send" onClick={() => send(input)}>↵</button>
        </div>
      </div>
    </div>
  );
}
