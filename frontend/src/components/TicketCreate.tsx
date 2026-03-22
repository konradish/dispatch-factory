import { useState, useEffect } from "react";
import { createTicket, createBacklogTicket } from "@/lib/api";

const FLAGS = [
  { value: "--no-merge", label: "No merge" },
  { value: "--plan", label: "Force planner" },
  { value: "--no-plan", label: "Skip planner" },
];

const PRIORITY_COLORS: Record<string, string> = {
  urgent: "text-accent-red",
  high: "text-accent-yellow",
  normal: "text-accent-blue",
  low: "text-gray-500",
};

interface TicketProposal {
  task: string;
  project: string;
  priority: string;
  flags: string[];
  related_repos: string[];
}

interface IntakeResponse {
  tickets: TicketProposal[];
  reasoning: string;
  questions: string[];
}

interface TicketCreateProps {
  onDispatched: (stdout: string) => void;
}

export default function TicketCreate({ onDispatched }: TicketCreateProps) {
  // Phase 1: raw input
  const [rawInput, setRawInput] = useState("");
  const [thinking, setThinking] = useState(false);

  // Phase 2: structured proposals (editable)
  const [intake, setIntake] = useState<IntakeResponse | null>(null);

  // Conversation history for multi-turn refinement
  const [conversationHistory, setConversationHistory] = useState<string[]>([]);
  const [refinementInput, setRefinementInput] = useState("");

  // Project suggestions
  const [projects, setProjects] = useState<string[]>([]);
  useEffect(() => {
    fetch("/api/projects").then((r) => r.json()).then(setProjects).catch(() => {});
  }, []);

  // Phase 3: results (per ticket)
  const [ticketResults, setTicketResults] = useState<Record<number, { type: string; message: string }>>({});
  const [error, setError] = useState<string | null>(null);

  async function handleIntake(e: React.FormEvent, extraContext?: string) {
    e.preventDefault();
    const inputText = rawInput.trim();
    if (!inputText) return;

    setThinking(true);
    setError(null);
    setTicketResults({});

    const contextParts = [...conversationHistory];
    if (extraContext) contextParts.push(extraContext);
    const context = contextParts.join("\n\n");

    try {
      const res = await fetch("/api/intake", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input: inputText, context }),
      });
      if (!res.ok) {
        const body = await res.text();
        setError(`${res.status}: ${body}`);
        setThinking(false);
        return;
      }
      const data: IntakeResponse = await res.json();
      setIntake(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reach intake");
    }
    setThinking(false);
  }

  function updateTicket(idx: number, field: keyof TicketProposal, value: unknown) {
    if (!intake) return;
    const tickets = [...intake.tickets];
    tickets[idx] = { ...tickets[idx], [field]: value };
    setIntake({ ...intake, tickets });
  }

  function toggleFlag(idx: number, flag: string) {
    if (!intake) return;
    const t = intake.tickets[idx];
    const flags = t.flags.includes(flag)
      ? t.flags.filter((f) => f !== flag)
      : [...t.flags, flag];
    updateTicket(idx, "flags", flags);
  }

  function removeTicket(idx: number) {
    if (!intake) return;
    const tickets = intake.tickets.filter((_, i) => i !== idx);
    setIntake({ ...intake, tickets });
  }

  async function handleDispatchOne(idx: number) {
    if (!intake) return;
    const t = intake.tickets[idx];
    setError(null);

    const res = await createTicket({ task: t.task, project: t.project, flags: t.flags });
    if (res.error) {
      setTicketResults((prev) => ({ ...prev, [idx]: { type: "error", message: res.error! } }));
    } else if (res.data?.status === "dispatched") {
      setTicketResults((prev) => ({ ...prev, [idx]: { type: "dispatched", message: "Dispatched" } }));
      onDispatched(res.data.stdout);
    } else {
      setTicketResults((prev) => ({ ...prev, [idx]: { type: "error", message: res.data?.stderr || "Failed" } }));
    }
  }

  async function handleQueueOne(idx: number) {
    if (!intake) return;
    const t = intake.tickets[idx];
    setError(null);

    // Set status based on whether ticket is fully specified
    const hasQuestions = intake.questions.length > 0;
    const isFullySpecified = t.task.trim() && t.project && t.project !== "unknown";
    const status = !isFullySpecified || hasQuestions ? "needs_input" : "ready";

    const res = await createBacklogTicket({ task: t.task, project: t.project, priority: t.priority, flags: t.flags, status });
    if (res.error) {
      setTicketResults((prev) => ({ ...prev, [idx]: { type: "error", message: res.error! } }));
    } else if (res.data) {
      setTicketResults((prev) => ({ ...prev, [idx]: { type: "queued", message: `Queued ${res.data!.id}` } }));
    }
  }

  async function handleQueueAll() {
    if (!intake) return;
    for (let i = 0; i < intake.tickets.length; i++) {
      if (!ticketResults[i]) await handleQueueOne(i);
    }
  }

  async function handleRefine() {
    if (!refinementInput.trim() || !intake) return;

    const questionsBlock = intake.questions.length > 0
      ? `AI asked: ${intake.questions.join("; ")}`
      : "";
    const answerBlock = `User answered: ${refinementInput.trim()}`;
    const newHistory = [...conversationHistory];
    if (questionsBlock) newHistory.push(questionsBlock);
    newHistory.push(answerBlock);
    setConversationHistory(newHistory);
    setRefinementInput("");

    const fakeEvent = { preventDefault: () => {} } as React.FormEvent;
    await handleIntake(fakeEvent, `${questionsBlock}\n${answerBlock}`);
  }

  function handleReset() {
    setIntake(null);
    setTicketResults({});
    setError(null);
    setConversationHistory([]);
    setRefinementInput("");
  }

  return (
    <div className="max-w-2xl mx-auto">
      {/* Phase 1: Raw input */}
      {!intake && Object.keys(ticketResults).length === 0 && (
        <form onSubmit={handleIntake} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">
              What do you need done?
            </label>
            <textarea
              value={rawInput}
              onChange={(e) => setRawInput(e.target.value.slice(0, 1000))}
              placeholder="Describe what you want in plain language. The intake assistant will help structure it into a dispatchable ticket."
              rows={4}
              required
              className="w-full bg-bg-surface border border-gray-700 rounded-lg px-4 py-3 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue/50 resize-none"
              autoFocus
            />
            <div className="text-xs text-gray-600 mt-1 text-right">
              {rawInput.length}/1000
            </div>
          </div>

          <button
            type="submit"
            disabled={thinking || !rawInput.trim()}
            className="w-full py-3 px-4 rounded-lg text-sm font-semibold transition-all bg-accent-purple/20 text-accent-purple border border-accent-purple/30 hover:bg-accent-purple/30 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {thinking ? (
              <span className="flex items-center justify-center gap-2">
                <span className="h-4 w-4 border-2 border-accent-purple/30 border-t-accent-purple rounded-full animate-spin" />
                Structuring ticket...
              </span>
            ) : (
              "Structure with AI"
            )}
          </button>

          <p className="text-xs text-gray-600 text-center">
            Or{" "}
            <button
              type="button"
              onClick={() =>
                setIntake({
                  tickets: [{ task: rawInput.trim(), project: "", priority: "normal", flags: [], related_repos: [] }],
                  reasoning: "",
                  questions: [],
                })
              }
              className="text-gray-400 hover:text-gray-200 underline"
            >
              skip AI and fill manually
            </button>
          </p>
        </form>
      )}

      {/* Phase 2: Structured proposals (editable) */}
      {intake && (
        <div className="space-y-4">
          {/* Reasoning */}
          {intake.reasoning && (
            <div className="bg-accent-purple/5 border border-accent-purple/20 rounded-lg px-4 py-3">
              <div className="text-[10px] uppercase text-accent-purple tracking-wider mb-1 font-semibold">
                AI reasoning
              </div>
              <p className="text-xs text-gray-400">{intake.reasoning}</p>
            </div>
          )}

          {/* Questions + refinement */}
          {intake.questions.length > 0 && (
            <div className="bg-accent-yellow/5 border border-accent-yellow/20 rounded-lg px-4 py-3 space-y-3">
              <div className="text-[10px] uppercase text-accent-yellow tracking-wider font-semibold">
                Clarifying questions
              </div>
              <ul className="text-xs text-gray-400 space-y-1">
                {intake.questions.map((q, i) => (
                  <li key={i}>• {q}</li>
                ))}
              </ul>
              <div className="flex gap-2 items-end">
                <textarea
                  value={refinementInput}
                  onChange={(e) => setRefinementInput(e.target.value)}
                  placeholder="Answer the questions... (Enter to submit, Shift+Enter for new line)"
                  rows={4}
                  className="flex-1 bg-bg-surface border border-gray-700 rounded px-3 py-2 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-accent-yellow resize-none"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey && refinementInput.trim()) {
                      e.preventDefault();
                      handleRefine();
                    }
                  }}
                />
                <button
                  onClick={handleRefine}
                  disabled={thinking || !refinementInput.trim()}
                  className="px-4 py-2 text-xs font-semibold rounded bg-accent-yellow/20 text-accent-yellow border border-accent-yellow/30 hover:bg-accent-yellow/30 disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
                >
                  {thinking ? "Refining..." : "Refine"}
                </button>
              </div>
            </div>
          )}

          {/* Ticket list */}
          <div className="text-xs text-gray-500 uppercase tracking-wider font-semibold">
            {intake.tickets.length} ticket{intake.tickets.length !== 1 ? "s" : ""}
          </div>

          {intake.tickets.map((t, idx) => {
            const result = ticketResults[idx];
            return (
              <div
                key={idx}
                className={`bg-bg-surface border rounded-lg p-4 space-y-3 ${
                  result?.type === "dispatched" ? "border-accent-green/30 opacity-60" :
                  result?.type === "queued" ? "border-accent-cyan/30 opacity-60" :
                  result?.type === "error" ? "border-accent-red/30" :
                  "border-gray-700"
                }`}
              >
                {result ? (
                  <div className={`text-xs font-semibold ${
                    result.type === "dispatched" ? "text-accent-green" :
                    result.type === "queued" ? "text-accent-cyan" : "text-accent-red"
                  }`}>
                    {result.message}
                  </div>
                ) : (
                  <>
                    <div className="flex items-start gap-3">
                      <textarea
                        value={t.task}
                        onChange={(e) => updateTicket(idx, "task", e.target.value.slice(0, 500))}
                        rows={2}
                        className="flex-1 bg-bg-base border border-gray-800 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-accent-blue resize-none"
                      />
                      <button
                        onClick={() => removeTicket(idx)}
                        className="text-gray-600 hover:text-accent-red text-xs shrink-0 pt-2"
                        title="Remove ticket"
                      >
                        ✕
                      </button>
                    </div>
                    <div className="flex gap-3 items-center">
                      <input
                        type="text"
                        value={t.project}
                        onChange={(e) => updateTicket(idx, "project", e.target.value)}
                        className="bg-bg-base border border-gray-800 rounded px-3 py-1.5 text-xs text-gray-200 mono focus:outline-none focus:border-accent-blue w-40"
                        list="project-suggestions"
                        placeholder="project"
                      />
                      <select
                        value={t.priority}
                        onChange={(e) => updateTicket(idx, "priority", e.target.value)}
                        className={`bg-bg-base border border-gray-800 rounded px-3 py-1.5 text-xs focus:outline-none ${PRIORITY_COLORS[t.priority] || "text-gray-200"}`}
                      >
                        <option value="low">low</option>
                        <option value="normal">normal</option>
                        <option value="high">high</option>
                        <option value="urgent">urgent</option>
                      </select>
                      {FLAGS.map((flag) => (
                        <label key={flag.value} className="flex items-center gap-1 text-[10px] text-gray-600 hover:text-gray-400 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={t.flags.includes(flag.value)}
                            onChange={() => toggleFlag(idx, flag.value)}
                            className="h-3 w-3 rounded border-gray-700 bg-bg-base"
                          />
                          <span className="mono">{flag.label}</span>
                        </label>
                      ))}
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleDispatchOne(idx)}
                        disabled={!t.task.trim() || !t.project}
                        className="px-3 py-1.5 text-xs font-semibold rounded bg-accent-green/20 text-accent-green border border-accent-green/30 hover:bg-accent-green/30 disabled:opacity-40"
                      >
                        Dispatch
                      </button>
                      <button
                        onClick={() => handleQueueOne(idx)}
                        disabled={!t.task.trim() || !t.project}
                        className="px-3 py-1.5 text-xs font-semibold rounded bg-accent-cyan/20 text-accent-cyan border border-accent-cyan/30 hover:bg-accent-cyan/30 disabled:opacity-40"
                      >
                        Queue
                      </button>
                    </div>
                  </>
                )}
              </div>
            );
          })}

          {/* Bulk actions + reset */}
          <div className="flex gap-3 pt-2">
            <button
              onClick={handleQueueAll}
              disabled={intake.tickets.every((_, i) => ticketResults[i])}
              className="flex-1 py-2.5 px-4 rounded-lg text-xs font-semibold transition-all bg-accent-cyan/10 text-accent-cyan border border-accent-cyan/20 hover:bg-accent-cyan/20 disabled:opacity-40"
            >
              Queue All
            </button>
            <button
              onClick={handleReset}
              className="px-4 py-2.5 text-xs text-gray-600 hover:text-gray-400 border border-gray-800 rounded-lg"
            >
              Start over
            </button>
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="mt-4 bg-accent-red/10 border border-accent-red/30 rounded-lg px-4 py-3 text-sm text-accent-red">
          {error}
        </div>
      )}
      {/* System prompt editor (collapsible) */}
      <SystemPromptEditor />

      {/* Project datalist for autocomplete */}
      <datalist id="project-suggestions">
        {projects.map((p) => (
          <option key={p} value={p} />
        ))}
      </datalist>
    </div>
  );
}

function SystemPromptEditor() {
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);

  function load() {
    if (loaded) return;
    fetch("/api/intake/prompt")
      .then((r) => r.json())
      .then((d) => { setPrompt(d.prompt); setLoaded(true); })
      .catch(() => {});
  }

  async function save() {
    setSaving(true);
    await fetch("/api/intake/prompt", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    });
    setSaving(false);
  }

  return (
    <div className="mt-6 border-t border-gray-800 pt-4">
      <button
        onClick={() => { setOpen(!open); load(); }}
        className="text-xs text-gray-600 hover:text-gray-400 flex items-center gap-1"
      >
        <span className="mono">{open ? "▾" : "▸"}</span>
        System Prompt
        <span className="text-gray-700 ml-1">(backend/intake-prompt.md)</span>
      </button>
      {open && (
        <div className="mt-3 space-y-2">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={16}
            className="w-full bg-bg-surface border border-gray-700 rounded-lg px-4 py-3 text-xs text-gray-300 mono focus:outline-none focus:border-accent-purple resize-y leading-relaxed"
          />
          <button
            onClick={save}
            disabled={saving}
            className="px-4 py-1.5 text-xs font-semibold rounded bg-accent-purple/20 text-accent-purple border border-accent-purple/30 hover:bg-accent-purple/30 disabled:opacity-40"
          >
            {saving ? "Saving..." : "Save Prompt"}
          </button>
        </div>
      )}
    </div>
  );
}
