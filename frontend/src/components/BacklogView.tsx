import { useState, useEffect, useCallback } from "react";
import type { BacklogTicket } from "@/types";
import {
  fetchBacklog,
  createBacklogTicket,
  deleteBacklogTicket,
  dispatchBacklogTicket,
} from "@/lib/api";

const FLAGS = [
  { value: "--no-merge", label: "Draft PR only" },
  { value: "--plan", label: "Force planner" },
  { value: "--no-plan", label: "Skip planner" },
];

const PRIORITY_ORDER: Record<string, number> = {
  urgent: 0,
  high: 1,
  normal: 2,
  low: 3,
};

const PRIORITY_COLORS: Record<string, string> = {
  urgent: "bg-accent-red text-white",
  high: "bg-accent-yellow/80 text-black",
  normal: "bg-accent-blue/80 text-white",
  low: "bg-gray-600 text-gray-200",
};

const STATUS_STYLES: Record<string, string> = {
  pending: "text-gray-400",
  dispatched: "text-accent-cyan",
  completed: "text-accent-green",
  failed: "text-accent-red",
  cancelled: "text-gray-500 line-through",
};

const PALETTE = [
  "#10b981",
  "#3b82f6",
  "#8b5cf6",
  "#f59e0b",
  "#06b6d4",
  "#ef4444",
  "#ec4899",
  "#f97316",
];
function projectColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++)
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

interface BacklogViewProps {
  onSelectSession: (sessionId: string) => void;
}

export default function BacklogView({ onSelectSession }: BacklogViewProps) {
  const [tickets, setTickets] = useState<BacklogTicket[]>([]);
  const [projects, setProjects] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Form state
  const [task, setTask] = useState("");
  const [project, setProject] = useState("");
  const [priority, setPriority] = useState("normal");
  const [flags, setFlags] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    const result = await fetchBacklog();
    if (result.error) {
      setError(result.error);
    } else if (result.data) {
      setTickets(result.data);
      setError(null);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, [load]);

  useEffect(() => {
    fetch("/api/projects")
      .then((r) => r.json())
      .then((data: string[]) => {
        setProjects(data);
        if (data.length > 0 && !project) setProject(data[0]);
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function toggleFlag(flag: string) {
    setFlags((prev) =>
      prev.includes(flag) ? prev.filter((f) => f !== flag) : [...prev, flag]
    );
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!task.trim()) return;

    setSubmitting(true);
    setError(null);
    const result = await createBacklogTicket({
      task: task.trim(),
      project,
      priority,
      flags,
    });
    setSubmitting(false);

    if (result.error) {
      setError(result.error);
    } else {
      setTask("");
      setFlags([]);
      setPriority("normal");
      load();
    }
  }

  async function handleDispatch(id: string) {
    const result = await dispatchBacklogTicket(id);
    if (result.error) {
      setError(result.error);
    } else {
      load();
    }
  }

  async function handleDelete(id: string) {
    const result = await deleteBacklogTicket(id);
    if (result.error) {
      setError(result.error);
    } else {
      load();
    }
  }

  const sorted = [...tickets].sort((a, b) => {
    const pa = PRIORITY_ORDER[a.priority] ?? 99;
    const pb = PRIORITY_ORDER[b.priority] ?? 99;
    if (pa !== pb) return pa - pb;
    return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
  });

  function formatTime(iso: string | null): string {
    if (!iso) return "--";
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="bg-accent-red/10 border border-accent-red/30 rounded-lg px-4 py-3 text-sm text-accent-red">
          {error}
        </div>
      )}

      {/* Create ticket form */}
      <div className="bg-bg-surface rounded-lg border border-gray-800 p-5">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
          Queue Ticket
        </h2>
        <form onSubmit={handleCreate} className="space-y-4">
          <div>
            <textarea
              value={task}
              onChange={(e) => setTask(e.target.value.slice(0, 500))}
              placeholder="Task description..."
              rows={3}
              required
              className="w-full bg-bg-base border border-gray-700 rounded-lg px-4 py-3 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue/50 resize-none"
            />
            <div className="text-xs text-gray-600 mt-1 text-right">
              {task.length}/500
            </div>
          </div>

          <div className="flex gap-4">
            <div className="flex-1">
              <label className="block text-xs text-gray-500 mb-1">
                Project
              </label>
              <select
                value={project}
                onChange={(e) => setProject(e.target.value)}
                className="w-full bg-bg-base border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 mono focus:outline-none focus:border-accent-blue"
              >
                {projects.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>
            <div className="w-36">
              <label className="block text-xs text-gray-500 mb-1">
                Priority
              </label>
              <select
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
                className="w-full bg-bg-base border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 mono focus:outline-none focus:border-accent-blue"
              >
                <option value="low">low</option>
                <option value="normal">normal</option>
                <option value="high">high</option>
                <option value="urgent">urgent</option>
              </select>
            </div>
          </div>

          <div className="flex items-center gap-5">
            {FLAGS.map((flag) => (
              <label
                key={flag.value}
                className="flex items-center gap-2 cursor-pointer text-xs text-gray-500 hover:text-gray-300"
              >
                <input
                  type="checkbox"
                  checked={flags.includes(flag.value)}
                  onChange={() => toggleFlag(flag.value)}
                  className="h-3.5 w-3.5 rounded border-gray-600 bg-bg-base text-accent-blue"
                />
                <span className="mono">{flag.value}</span>
              </label>
            ))}
          </div>

          <button
            type="submit"
            disabled={submitting || !task.trim()}
            className="px-5 py-2 rounded text-sm font-medium bg-accent-blue/20 text-accent-blue border border-accent-blue/30 hover:bg-accent-blue/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {submitting ? "Adding..." : "Add to Backlog"}
          </button>
        </form>
      </div>

      {/* Ticket list */}
      <div>
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
          Queue ({tickets.length})
        </h2>
        {loading ? (
          <div className="flex items-center justify-center py-12 text-gray-500">
            <div className="h-5 w-5 border-2 border-gray-600 border-t-accent-green rounded-full animate-spin mr-3" />
            Loading backlog...
          </div>
        ) : sorted.length === 0 ? (
          <div className="bg-bg-surface rounded-lg border border-gray-800 p-8 text-center text-gray-500">
            Backlog is empty. Add a ticket above.
          </div>
        ) : (
          <div className="bg-bg-surface rounded-lg border border-gray-800 overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[10px] text-gray-600 uppercase tracking-wider border-b border-gray-800">
                  <th className="text-left py-2 px-3 w-16">Priority</th>
                  <th className="text-left py-2 px-3 w-24">Project</th>
                  <th className="text-left py-2 px-3">Task</th>
                  <th className="text-left py-2 px-3 w-20">Status</th>
                  <th className="text-left py-2 px-3 w-28">Created</th>
                  <th className="text-right py-2 px-3 w-40">Actions</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((ticket) => (
                  <tr
                    key={ticket.id}
                    className="border-b border-gray-800/30 hover:bg-bg-surface-alt/30 transition-colors"
                  >
                    <td className="py-2 px-3">
                      <span
                        className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold mono uppercase ${
                          PRIORITY_COLORS[ticket.priority] ?? "bg-gray-600"
                        }`}
                      >
                        {ticket.priority}
                      </span>
                    </td>
                    <td className="py-2 px-3">
                      <div className="flex items-center gap-2">
                        <div
                          className="h-2 w-2 rounded-full shrink-0"
                          style={{
                            backgroundColor: projectColor(ticket.project),
                          }}
                        />
                        <span className="mono text-gray-300">
                          {ticket.project}
                        </span>
                      </div>
                    </td>
                    <td className="py-2 px-3 text-gray-400 max-w-xs truncate">
                      {ticket.task}
                    </td>
                    <td className="py-2 px-3">
                      <span
                        className={`mono ${
                          STATUS_STYLES[ticket.status] ?? "text-gray-500"
                        }`}
                      >
                        {ticket.status}
                      </span>
                    </td>
                    <td className="py-2 px-3 mono text-gray-500">
                      {formatTime(ticket.created_at)}
                    </td>
                    <td className="py-2 px-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        {ticket.status === "pending" && (
                          <>
                            <button
                              onClick={() => handleDispatch(ticket.id)}
                              className="px-2.5 py-1 rounded text-[11px] font-medium bg-accent-green/20 text-accent-green border border-accent-green/30 hover:bg-accent-green/30 transition-colors"
                            >
                              Dispatch Now
                            </button>
                            <button
                              onClick={() => handleDelete(ticket.id)}
                              className="px-2 py-1 rounded text-[11px] text-gray-500 hover:text-accent-red hover:bg-accent-red/10 transition-colors"
                            >
                              Delete
                            </button>
                          </>
                        )}
                        {ticket.status === "dispatched" &&
                          ticket.session_id && (
                            <button
                              onClick={() =>
                                onSelectSession(ticket.session_id!)
                              }
                              className="mono text-[11px] text-accent-cyan hover:text-accent-cyan/80 transition-colors"
                            >
                              {ticket.session_id}
                            </button>
                          )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
