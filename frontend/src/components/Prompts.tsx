import { useState, useEffect, useCallback } from "react";
import { api } from "../api";

interface Prompt {
  name: string;
  group: string;
  raw: string;
  triggers?: string;
  description?: string;
  notes?: string;
  used_by?: string;
}

const PROMPT_LABELS: Record<string, { icon: string; title: string; color: string }> = {
  summarizer_system:    { icon: "◈", title: "Summarizer — system",    color: "var(--rust)" },
  summarizer_user:      { icon: "◈", title: "Summarizer — user",      color: "var(--rust)" },
  chat_system:          { icon: "⌖", title: "Chat assistant — system", color: "var(--ok)" },
  general_system:       { icon: "⌖", title: "General LLM — system",    color: "var(--ok)" },
  agent_system:         { icon: "⚡", title: "Agent — orchestrator",    color: "var(--ok)" },
  ocr_page:             { icon: "⇲", title: "OCR — page prompt",       color: "var(--ink-3)" },
  "agents/processing":  { icon: "⚙", title: "Agent — processing",     color: "#e07020" },
  "agents/knowledge":   { icon: "◈", title: "Agent — knowledge",      color: "#4db6ac" },
  "agents/analysis":    { icon: "⌬", title: "Agent — analysis",       color: "#a8c97a" },
};

const SKILL_TEMPLATE = `---
description: >
  Short description of what this skill does.
used_by: agent
triggers: keyword1, keyword2
notes: >
  Injected into the agent system prompt when the user message contains one of the triggers.
---

## Skill title

Instructions for the agent go here.
`;

function NewSkillForm({ onCreate, onCancel }: { onCreate: (skill: Prompt) => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [raw, setRaw] = useState(SKILL_TEMPLATE);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const safeName = name.trim().toLowerCase().replace(/\s+/g, "_");
    if (!safeName) { setErr("Name is required."); return; }
    setSaving(true); setErr(null);
    try {
      const created = await api.createSkill(safeName, raw) as Prompt;
      onCreate(created);
    } catch (e) {
      setErr((e as Error).message);
    }
    setSaving(false);
  };

  return (
    <div style={{ position: "absolute", inset: 0, background: "rgba(36,28,18,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 40 }}>
      <div style={{ background: "var(--bg)", border: "1px solid var(--rule-2)", borderRadius: 10, padding: "24px 28px", width: 520, maxWidth: "90vw", display: "flex", flexDirection: "column", gap: 14 }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: "var(--ink)" }}>New skill</div>
        <div>
          <label style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: 0.6 }}>
            Skill name (used as filename)
          </label>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. citation_style"
            style={{ display: "block", width: "100%", marginTop: 6, padding: "7px 10px", borderRadius: 5, border: "1px solid var(--rule-2)", fontSize: 12, fontFamily: "var(--font-mono)", background: "var(--bg-1)", color: "var(--ink)", outline: "none" }} />
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <label style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-4)", textTransform: "uppercase", letterSpacing: 0.6 }}>
            Content (frontmatter + body)
          </label>
          <textarea value={raw} onChange={e => setRaw(e.target.value)} spellCheck={false} rows={14}
            style={{ resize: "vertical", padding: "10px 12px", borderRadius: 5, border: "1px solid var(--rule-2)", fontSize: 11, fontFamily: "var(--font-mono)", lineHeight: 1.6, background: "var(--bg-1)", color: "var(--ink)", outline: "none" }} />
        </div>
        {err && <div style={{ fontSize: 11, color: "var(--rust)" }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="ghost" onClick={onCancel} disabled={saving} style={{ fontSize: 12 }}>Cancel</button>
          <button className="primary" onClick={() => void submit()} disabled={saving} style={{ fontSize: 12 }}>{saving ? "Creating…" : "Create skill"}</button>
        </div>
      </div>
    </div>
  );
}

export function PromptsPage() {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [selected, setSelected] = useState<Prompt | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showNewSkill, setShowNewSkill] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const corePrompts = prompts.filter(p => p.group === "core");
  const agentPrompts = prompts.filter(p => p.group === "agent");
  const skills = prompts.filter(p => p.group === "skill");

  useEffect(() => {
    api.fetchPrompts().then(raw => {
      const list = raw as Prompt[];
      setPrompts(list);
      if (list.length && !selected) {
        setSelected(list[0]);
        setDraft(list[0].raw);
      }
    }).catch(() => {});
  }, []);

  const select = useCallback((p: Prompt) => {
    setSelected(p);
    setDraft(p.raw);
    setSaved(false);
    setErr(null);
    setDeleteConfirm(null);
  }, []);

  const save = async () => {
    if (!selected) return;
    setSaving(true); setErr(null); setSaved(false);
    const updated = await api.savePrompt(selected.name, draft).catch(() => null) as Prompt | null;
    if (updated) {
      setPrompts(ps => ps.map(p => p.name === updated.name ? updated : p));
      setSelected(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } else {
      setErr("Save failed — check server logs.");
    }
    setSaving(false);
  };

  const onSkillCreated = (skill: Prompt) => {
    setPrompts(ps => [...ps, skill]);
    setSelected(skill);
    setDraft(skill.raw);
    setSaved(false);
    setErr(null);
    setShowNewSkill(false);
  };

  const doDelete = async (p: Prompt) => {
    try {
      await api.deletePrompt(p.name);
      const remaining = prompts.filter(x => x.name !== p.name);
      setPrompts(remaining);
      if (selected?.name === p.name) {
        const next = remaining[0] || null;
        setSelected(next);
        setDraft(next?.raw || "");
      }
      setDeleteConfirm(null);
    } catch (e) {
      setErr((e as Error).message);
      setDeleteConfirm(null);
    }
  };

  const dirty = selected && draft !== selected.raw;

  const renderListItem = (p: Prompt, isSkill: boolean) => {
    const lbl = PROMPT_LABELS[p.name] || {
      icon: isSkill ? "◇" : "◆",
      title: isSkill ? p.name.replace("skills/", "") : p.name,
      color: isSkill ? "var(--sulfur)" : "var(--ink-3)",
    };
    const active = selected?.name === p.name;
    const isConfirming = deleteConfirm === p.name;

    return (
      <div key={p.name} onClick={() => select(p)} style={{
        padding: "9px 14px 9px 16px", cursor: "pointer",
        borderBottom: "1px solid var(--rule)",
        background: active ? "var(--bg-2)" : "transparent",
        borderLeft: active ? "2px solid var(--rust)" : "2px solid transparent",
        transition: "background 0.1s",
        display: "flex", alignItems: "flex-start", gap: 6,
      }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 5, marginBottom: 2 }}>
            <span style={{ color: lbl.color, fontSize: 12 }}>{lbl.icon}</span>
            <span style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-1)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {lbl.title}
            </span>
          </div>
          {isSkill && p.triggers && (
            <div style={{ fontSize: 10, color: "var(--ink-4)", fontFamily: "var(--font-mono)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              triggers: {p.triggers}
            </div>
          )}
          {!isSkill && p.group === "agent" && p.description && (
            <div style={{ fontSize: 10, color: "var(--ink-4)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.description}</div>
          )}
          {!isSkill && p.group !== "agent" && p.used_by && (
            <div style={{ fontSize: 10, color: "var(--ink-4)", fontFamily: "var(--font-mono)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.used_by}</div>
          )}
        </div>
        {isSkill && (
          <div onClick={e => { e.stopPropagation(); setDeleteConfirm(isConfirming ? null : p.name); }}
               style={{ flexShrink: 0, fontSize: 11, color: isConfirming ? "var(--rust)" : "var(--ink-5)", cursor: "pointer", padding: "1px 4px", borderRadius: 3, background: isConfirming ? "color-mix(in srgb, var(--rust) 10%, transparent)" : "transparent" }}
               title="Delete skill">
            {isConfirming ? (
              <span onClick={e => { e.stopPropagation(); void doDelete(p); }} style={{ fontWeight: 600 }}>✕ delete?</span>
            ) : "⋯"}
          </div>
        )}
      </div>
    );
  };

  return (
    <div style={{ display: "flex", height: "100%", overflow: "hidden", position: "relative" }}>
      {showNewSkill && <NewSkillForm onCreate={onSkillCreated} onCancel={() => setShowNewSkill(false)} />}

      <div style={{ width: 260, flexShrink: 0, borderRight: "1px solid var(--rule)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{ padding: "14px 16px 10px", borderBottom: "1px solid var(--rule)", fontSize: 11, fontWeight: 600, letterSpacing: 0.8, color: "var(--ink-4)", textTransform: "uppercase", flexShrink: 0 }}>
          Prompts · {corePrompts.length}
        </div>
        <div style={{ overflowY: "auto", flex: "0 0 auto", maxHeight: "50%" }}>
          {corePrompts.map(p => renderListItem(p, false))}
        </div>

        <div style={{ padding: "10px 16px 8px", borderTop: "1px solid var(--rule-2)", borderBottom: "1px solid var(--rule)", flexShrink: 0 }}>
          <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: 0.8, color: "var(--ink-4)", textTransform: "uppercase" }}>
            Agents · {agentPrompts.length}
          </span>
        </div>
        <div style={{ overflowY: "auto", flex: "0 0 auto", maxHeight: "30%" }}>
          {agentPrompts.map(p => renderListItem(p, false))}
        </div>

        <div style={{ padding: "10px 16px 8px", borderTop: "1px solid var(--rule-2)", borderBottom: "1px solid var(--rule)", display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
          <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: 0.8, color: "var(--ink-4)", textTransform: "uppercase" }}>
            Skills · {skills.length}
          </span>
          <button onClick={() => setShowNewSkill(true)}
            style={{ fontSize: 11, padding: "2px 8px", borderRadius: 4, border: "1px solid var(--rule-2)", background: "var(--bg-1)", color: "var(--sulfur)", cursor: "pointer", fontFamily: "var(--font)" }}
            title="Create new skill">
            ＋ New
          </button>
        </div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {skills.length === 0 ? (
            <div style={{ padding: "12px 16px", fontSize: 11, color: "var(--ink-5)", fontStyle: "italic" }}>No skills yet.</div>
          ) : (
            skills.map(p => renderListItem(p, true))
          )}
        </div>

        <div style={{ padding: "10px 14px", borderTop: "1px solid var(--rule)", fontSize: 10, color: "var(--ink-4)", lineHeight: 1.5, flexShrink: 0 }}>
          Edits take effect on the next agent or summarisation request.
        </div>
      </div>

      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {selected ? (
          <>
            <div style={{ padding: "10px 20px", borderBottom: "1px solid var(--rule)", display: "flex", alignItems: "center", gap: 12, flexShrink: 0 }}>
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "var(--ink-1)" }}>
                    {selected.group === "skill" ? selected.name.replace("skills/", "") : selected.group === "agent" ? selected.name.replace("agents/", "") : (PROMPT_LABELS[selected.name] || {}).title || selected.name}
                  </div>
                  {selected.group === "skill" && (
                    <span style={{ fontSize: 10, padding: "1px 7px", borderRadius: 20, background: "color-mix(in srgb, var(--sulfur) 12%, transparent)", border: "1px solid color-mix(in srgb, var(--sulfur) 28%, transparent)", color: "var(--sulfur)", fontWeight: 600, letterSpacing: 0.4 }}>skill</span>
                  )}
                  {selected.group === "agent" && (
                    <span style={{ fontSize: 10, padding: "1px 7px", borderRadius: 20, background: "color-mix(in srgb, var(--ok) 12%, transparent)", border: "1px solid color-mix(in srgb, var(--ok) 28%, transparent)", color: "var(--ok)", fontWeight: 600, letterSpacing: 0.4 }}>agent</span>
                  )}
                </div>
                {selected.description && <div style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 2 }}>{selected.description}</div>}
                {selected.group === "skill" && selected.triggers && (
                  <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 3, fontFamily: "var(--font-mono)" }}>triggers: {selected.triggers}</div>
                )}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                {err && <span style={{ fontSize: 11, color: "var(--rust)" }}>{err}</span>}
                {saved && <span style={{ fontSize: 11, color: "var(--ok)" }}>✓ Saved</span>}
                {dirty && !saving && <span style={{ fontSize: 10, color: "var(--ink-4)", fontStyle: "italic" }}>unsaved changes</span>}
                <button className={dirty ? "primary" : "ghost"} disabled={saving || !dirty} onClick={() => void save()} style={{ whiteSpace: "nowrap", fontSize: 12 }}>
                  {saving ? "Saving…" : "↑ Save"}
                </button>
              </div>
            </div>

            {selected.notes && (
              <div style={{ padding: "7px 20px", background: "var(--bg-2)", borderBottom: "1px solid var(--rule)", fontSize: 11, color: "var(--ink-3)", lineHeight: 1.5 }}>
                ℹ {selected.notes}
              </div>
            )}

            <textarea
              value={draft}
              onChange={e => { setDraft(e.target.value); setSaved(false); }}
              spellCheck={false}
              style={{ flex: 1, resize: "none", border: "none", outline: "none", padding: "16px 20px", fontFamily: "var(--font-mono)", fontSize: 12, lineHeight: 1.65, background: "var(--bg)", color: "var(--ink-1)", overflowY: "auto" }}
            />
          </>
        ) : (
          <div style={{ padding: 40, color: "var(--ink-4)", fontSize: 13 }}>Select a prompt to view or edit it.</div>
        )}
      </div>
    </div>
  );
}
