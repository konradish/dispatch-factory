import { useState } from "react";
import { createTicket } from "@/lib/api";
import type { TicketResponse } from "@/types";
// TicketResponse has { status, stdout, stderr } from the real backend

const PROJECTS = [
  "recipebrain",
  "schoolbrain",
  "movies",
  "lawpass",
  "electricapp",
  "voice-bridge",
];

const FLAGS = [
  { value: "--no-merge", label: "No merge (draft PR only)" },
  { value: "--plan", label: "Force planner" },
  { value: "--no-plan", label: "Skip planner" },
];

export default function TicketCreate() {
  const [task, setTask] = useState("");
  const [project, setProject] = useState(PROJECTS[0]);
  const [flags, setFlags] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<TicketResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  function toggleFlag(flag: string) {
    setFlags((prev) =>
      prev.includes(flag) ? prev.filter((f) => f !== flag) : [...prev, flag]
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!task.trim()) return;

    setSubmitting(true);
    setError(null);
    setResult(null);

    const res = await createTicket({ task: task.trim(), project, flags });
    setSubmitting(false);

    if (res.error) {
      setError(res.error);
    } else if (res.data) {
      setResult(res.data);
      if (res.data.status === "dispatched") {
        setTask("");
        setFlags([]);
      }
    }
  }

  return (
    <div className="max-w-2xl mx-auto">
      {/* Warning banner */}
      <div className="bg-accent-yellow/10 border border-accent-yellow/30 rounded-lg px-4 py-3 mb-6 text-sm text-accent-yellow">
        Controls require the backend to be running with controls enabled. The
        API will return 403 if controls are not enabled in
        .dispatch-factory.toml.
      </div>

      <form onSubmit={handleSubmit} className="space-y-5">
        {/* Task */}
        <div>
          <label
            htmlFor="task"
            className="block text-sm font-medium text-gray-300 mb-2"
          >
            Task Description
          </label>
          <textarea
            id="task"
            value={task}
            onChange={(e) => setTask(e.target.value.slice(0, 500))}
            placeholder="What should the worker do?"
            rows={4}
            required
            className="w-full bg-bg-surface border border-gray-700 rounded-lg px-4 py-3 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue/50 resize-none"
          />
          <div className="text-xs text-gray-600 mt-1 text-right">
            {task.length}/500
          </div>
        </div>

        {/* Project */}
        <div>
          <label
            htmlFor="project"
            className="block text-sm font-medium text-gray-300 mb-2"
          >
            Project
          </label>
          <select
            id="project"
            value={project}
            onChange={(e) => setProject(e.target.value)}
            className="w-full bg-bg-surface border border-gray-700 rounded-lg px-4 py-3 text-sm text-gray-200 focus:outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue/50 mono"
          >
            {PROJECTS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>

        {/* Flags */}
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-2">
            Flags
          </label>
          <div className="space-y-2">
            {FLAGS.map((flag) => (
              <label
                key={flag.value}
                className="flex items-center gap-3 cursor-pointer group"
              >
                <input
                  type="checkbox"
                  checked={flags.includes(flag.value)}
                  onChange={() => toggleFlag(flag.value)}
                  className="h-4 w-4 rounded border-gray-600 bg-bg-surface text-accent-blue focus:ring-accent-blue/50"
                />
                <span className="text-sm text-gray-400 group-hover:text-gray-300 transition-colors">
                  <span className="mono text-xs text-gray-500">
                    {flag.value}
                  </span>{" "}
                  &mdash; {flag.label}
                </span>
              </label>
            ))}
          </div>
        </div>

        {/* Submit */}
        <button
          type="submit"
          disabled={submitting || !task.trim()}
          className="w-full py-3 px-4 rounded-lg text-sm font-semibold transition-all bg-accent-green/20 text-accent-green border border-accent-green/30 hover:bg-accent-green/30 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {submitting ? (
            <span className="flex items-center justify-center gap-2">
              <span className="h-4 w-4 border-2 border-accent-green/30 border-t-accent-green rounded-full animate-spin" />
              Dispatching...
            </span>
          ) : (
            "Dispatch"
          )}
        </button>
      </form>

      {/* Error */}
      {error && (
        <div className="mt-4 bg-accent-red/10 border border-accent-red/30 rounded-lg px-4 py-3 text-sm text-accent-red">
          {error}
        </div>
      )}

      {/* Success */}
      {result && result.status === "dispatched" && (
        <div className="mt-4 bg-accent-green/10 border border-accent-green/30 rounded-lg px-4 py-4 space-y-3">
          <div className="text-sm text-accent-green">Dispatched successfully</div>
          {result.stdout && (
            <pre className="mono text-xs text-gray-400 whitespace-pre-wrap">{result.stdout}</pre>
          )}
        </div>
      )}
    </div>
  );
}
