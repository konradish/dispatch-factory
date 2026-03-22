import { useState } from "react";
import { createTicket, createBacklogTicket } from "@/lib/api";

const FLAGS = [
  { value: "--no-merge", label: "No merge (draft PR only)" },
  { value: "--plan", label: "Force planner" },
  { value: "--no-plan", label: "Skip planner" },
];

interface IntakeProposal {
  task: string;
  project: string;
  priority: string;
  flags: string[];
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

  // Phase 2: structured proposal (editable)
  const [proposal, setProposal] = useState<IntakeProposal | null>(null);

  // Conversation history for multi-turn refinement
  const [conversationHistory, setConversationHistory] = useState<string[]>([]);
  const [refinementInput, setRefinementInput] = useState("");

  // Phase 3: result
  const [dispatching, setDispatching] = useState(false);
  const [result, setResult] = useState<{ type: "dispatched" | "queued" | "error"; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleIntake(e: React.FormEvent, extraContext?: string) {
    e.preventDefault();
    const inputText = rawInput.trim();
    if (!inputText) return;

    setThinking(true);
    setError(null);
    setResult(null);

    // Build context from conversation history
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
      const data = await res.json();
      setProposal(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reach intake");
    }
    setThinking(false);
  }

  function updateProposal(field: keyof IntakeProposal, value: unknown) {
    if (!proposal) return;
    setProposal({ ...proposal, [field]: value });
  }

  function toggleFlag(flag: string) {
    if (!proposal) return;
    const flags = proposal.flags.includes(flag)
      ? proposal.flags.filter((f) => f !== flag)
      : [...proposal.flags, flag];
    updateProposal("flags", flags);
  }

  async function handleDispatch() {
    if (!proposal) return;
    setDispatching(true);
    setError(null);

    const res = await createTicket({
      task: proposal.task,
      project: proposal.project,
      flags: proposal.flags,
    });

    setDispatching(false);
    if (res.error) {
      setError(res.error);
    } else if (res.data?.status === "dispatched") {
      setResult({ type: "dispatched", message: "Dispatched" });
      onDispatched(res.data.stdout);
      setRawInput("");
      setProposal(null);
    } else {
      setError(res.data?.stderr || "Dispatch failed");
    }
  }

  async function handleQueue() {
    if (!proposal) return;
    setDispatching(true);
    setError(null);

    const res = await createBacklogTicket({
      task: proposal.task,
      project: proposal.project,
      priority: proposal.priority,
      flags: proposal.flags,
    });

    setDispatching(false);
    if (res.error) {
      setError(res.error);
    } else if (res.data) {
      setResult({ type: "queued", message: `Queued as ${res.data.id}` });
      setRawInput("");
      setProposal(null);
    }
  }

  async function handleRefine() {
    if (!refinementInput.trim() || !proposal) return;

    // Add the AI's questions + user's answers to conversation history
    const questionsBlock = proposal.questions.length > 0
      ? `AI asked: ${proposal.questions.join("; ")}`
      : "";
    const answerBlock = `User answered: ${refinementInput.trim()}`;
    const newHistory = [...conversationHistory];
    if (questionsBlock) newHistory.push(questionsBlock);
    newHistory.push(answerBlock);
    setConversationHistory(newHistory);
    setRefinementInput("");

    // Re-run intake with the accumulated context
    const fakeEvent = { preventDefault: () => {} } as React.FormEvent;
    await handleIntake(fakeEvent, `${questionsBlock}\n${answerBlock}`);
  }

  function handleReset() {
    setProposal(null);
    setResult(null);
    setError(null);
    setConversationHistory([]);
    setRefinementInput("");
  }

  return (
    <div className="max-w-2xl mx-auto">
      {/* Phase 1: Raw input */}
      {!proposal && !result && (
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
                setProposal({
                  task: rawInput.trim(),
                  project: "",
                  priority: "normal",
                  flags: [],
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

      {/* Phase 2: Structured proposal (editable) */}
      {proposal && !result && (
        <div className="space-y-4">
          {/* Reasoning */}
          {proposal.reasoning && (
            <div className="bg-accent-purple/5 border border-accent-purple/20 rounded-lg px-4 py-3">
              <div className="text-[10px] uppercase text-accent-purple tracking-wider mb-1 font-semibold">
                AI reasoning
              </div>
              <p className="text-xs text-gray-400">{proposal.reasoning}</p>
            </div>
          )}

          {/* Questions + refinement */}
          {proposal.questions.length > 0 && (
            <div className="bg-accent-yellow/5 border border-accent-yellow/20 rounded-lg px-4 py-3 space-y-3">
              <div className="text-[10px] uppercase text-accent-yellow tracking-wider font-semibold">
                Clarifying questions
              </div>
              <ul className="text-xs text-gray-400 space-y-1">
                {proposal.questions.map((q, i) => (
                  <li key={i}>• {q}</li>
                ))}
              </ul>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={refinementInput}
                  onChange={(e) => setRefinementInput(e.target.value)}
                  placeholder="Answer the questions to refine the ticket..."
                  className="flex-1 bg-bg-surface border border-gray-700 rounded px-3 py-2 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-accent-yellow"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && refinementInput.trim()) {
                      e.preventDefault();
                      handleRefine();
                    }
                  }}
                />
                <button
                  onClick={handleRefine}
                  disabled={thinking || !refinementInput.trim()}
                  className="px-4 py-2 text-xs font-semibold rounded bg-accent-yellow/20 text-accent-yellow border border-accent-yellow/30 hover:bg-accent-yellow/30 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {thinking ? "Refining..." : "Refine"}
                </button>
              </div>
            </div>
          )}

          {/* Editable fields */}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">
              Task
            </label>
            <textarea
              value={proposal.task}
              onChange={(e) => updateProposal("task", e.target.value.slice(0, 500))}
              rows={3}
              className="w-full bg-bg-surface border border-gray-700 rounded-lg px-4 py-3 text-sm text-gray-200 focus:outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue/50 resize-none"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1">
                Project
              </label>
              <input
                type="text"
                value={proposal.project}
                onChange={(e) => updateProposal("project", e.target.value)}
                className="w-full bg-bg-surface border border-gray-700 rounded-lg px-4 py-2.5 text-sm text-gray-200 mono focus:outline-none focus:border-accent-blue"
                list="project-suggestions"
              />
              <ProjectDatalist />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1">
                Priority
              </label>
              <select
                value={proposal.priority}
                onChange={(e) => updateProposal("priority", e.target.value)}
                className="w-full bg-bg-surface border border-gray-700 rounded-lg px-4 py-2.5 text-sm text-gray-200 focus:outline-none focus:border-accent-blue"
              >
                <option value="low">Low</option>
                <option value="normal">Normal</option>
                <option value="high">High</option>
                <option value="urgent">Urgent</option>
              </select>
            </div>
          </div>

          {/* Flags */}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">
              Flags
            </label>
            <div className="flex gap-4">
              {FLAGS.map((flag) => (
                <label
                  key={flag.value}
                  className="flex items-center gap-2 cursor-pointer text-xs text-gray-500 hover:text-gray-300"
                >
                  <input
                    type="checkbox"
                    checked={proposal.flags.includes(flag.value)}
                    onChange={() => toggleFlag(flag.value)}
                    className="h-3.5 w-3.5 rounded border-gray-600 bg-bg-surface"
                  />
                  <span className="mono">{flag.value}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <button
              onClick={handleDispatch}
              disabled={dispatching || !proposal.task.trim() || !proposal.project}
              className="flex-1 py-3 px-4 rounded-lg text-sm font-semibold transition-all bg-accent-green/20 text-accent-green border border-accent-green/30 hover:bg-accent-green/30 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {dispatching ? "Dispatching..." : "Dispatch Now"}
            </button>
            <button
              onClick={handleQueue}
              disabled={dispatching || !proposal.task.trim() || !proposal.project}
              className="flex-1 py-3 px-4 rounded-lg text-sm font-semibold transition-all bg-accent-cyan/20 text-accent-cyan border border-accent-cyan/30 hover:bg-accent-cyan/30 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {dispatching ? "Queuing..." : "Add to Backlog"}
            </button>
          </div>
          <button
            onClick={handleReset}
            className="w-full text-xs text-gray-600 hover:text-gray-400 py-1"
          >
            Start over
          </button>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="mt-4 bg-accent-red/10 border border-accent-red/30 rounded-lg px-4 py-3 text-sm text-accent-red">
          {error}
        </div>
      )}

      {/* Result */}
      {result && (
        <div
          className={`mt-4 rounded-lg px-4 py-4 space-y-3 ${
            result.type === "dispatched"
              ? "bg-accent-green/10 border border-accent-green/30"
              : result.type === "queued"
                ? "bg-accent-cyan/10 border border-accent-cyan/30"
                : "bg-accent-red/10 border border-accent-red/30"
          }`}
        >
          <div
            className={`text-sm ${
              result.type === "dispatched"
                ? "text-accent-green"
                : result.type === "queued"
                  ? "text-accent-cyan"
                  : "text-accent-red"
            }`}
          >
            {result.message}
          </div>
          <button
            onClick={() => {
              setResult(null);
              setRawInput("");
            }}
            className="text-xs text-gray-500 hover:text-gray-300 underline"
          >
            Create another
          </button>
        </div>
      )}
    </div>
  );
}

function ProjectDatalist() {
  const [projects, setProjects] = useState<string[]>([]);
  useState(() => {
    fetch("/api/projects")
      .then((r) => r.json())
      .then(setProjects)
      .catch(() => {});
  });
  return (
    <datalist id="project-suggestions">
      {projects.map((p) => (
        <option key={p} value={p} />
      ))}
    </datalist>
  );
}
