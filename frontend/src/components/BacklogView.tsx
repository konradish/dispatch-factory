import { useState, useEffect, useCallback, useRef } from "react";
import type { BacklogTicket, SelfImprovementState } from "@/types";
import {
  fetchBacklog,
  createBacklogTicket,
  dispatchBacklogTicket,
  updateBacklogTicket,
  addTicketNote,
  fetchSelfImprovement,
} from "@/lib/api";
import { validateTaskQuality, TASK_MIN_LENGTH } from "@/lib/taskQuality";

// -- Constants ----------------------------------------------------------------

const PALETTE = [
  "#10b981", "#3b82f6", "#8b5cf6", "#f59e0b",
  "#06b6d4", "#ef4444", "#ec4899", "#f97316",
];

function projectColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++)
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

const PRIORITY_COLORS: Record<string, string> = {
  urgent: "text-accent-red",
  high: "text-accent-yellow",
  normal: "text-accent-blue",
  low: "text-gray-500",
};

const PRIORITY_BG: Record<string, string> = {
  urgent: "bg-accent-red/10 border-accent-red/40",
  high: "bg-accent-yellow/5 border-accent-yellow/30",
  normal: "bg-bg-surface border-gray-700",
  low: "bg-bg-surface border-gray-800",
};

interface ColumnDef {
  id: string;
  title: string;
  statuses: string[];
  borderColor: string;
  collapsible?: boolean;
  pulse?: boolean;
}

const COLUMNS: ColumnDef[] = [
  { id: "intake", title: "Intake", statuses: ["intake", "needs_input"], borderColor: "border-accent-yellow" },
  { id: "ready", title: "Ready", statuses: ["ready", "pending"], borderColor: "border-accent-blue" },
  { id: "inflight", title: "In Flight", statuses: ["dispatching", "dispatched"], borderColor: "border-accent-cyan", pulse: true },
  { id: "done", title: "Done", statuses: ["completed", "failed", "cancelled", "blocked"], borderColor: "border-accent-green", collapsible: true },
];


const PRIORITY_ORDER: Record<string, number> = {
  urgent: 0, high: 1, normal: 2, low: 3,
};

// -- Helpers ------------------------------------------------------------------

function formatTime(ts: number | string | null): string {
  if (!ts) return "--";
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  const now = Date.now();
  const diff = now - d.getTime();
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

function statusBorderColor(status: string): string {
  switch (status) {
    case "completed": return "border-l-accent-green";
    case "failed": return "border-l-accent-red";
    case "cancelled": return "border-l-gray-600";
    default: return "border-l-transparent";
  }
}

// -- Props --------------------------------------------------------------------

interface BacklogViewProps {
  onSelectSession: (sessionId: string) => void;
}

// -- Component ----------------------------------------------------------------

export default function BacklogView({ onSelectSession }: BacklogViewProps) {
  const [tickets, setTickets] = useState<BacklogTicket[]>([]);
  const [projects, setProjects] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [doneExpanded, setDoneExpanded] = useState(false);
  const [selfImprovement, setSelfImprovement] = useState<SelfImprovementState | null>(null);

  // Quick-add form
  const [task, setTask] = useState("");
  const [project, setProject] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Ticket detail
  const [selectedTicket, setSelectedTicket] = useState<BacklogTicket | null>(null);
  const [noteText, setNoteText] = useState("");
  const [noteSending, setNoteSending] = useState(false);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    const result = await fetchBacklog();
    if (result.error) {
      setError(result.error);
    } else if (result.data) {
      setTickets(result.data);
      setError(null);
    }
    setLoading(false);
    const siResult = await fetchSelfImprovement();
    if (siResult.data) setSelfImprovement(siResult.data);
  }, []);

  useEffect(() => {
    load();
    pollRef.current = setInterval(load, 5000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
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

  // -- Actions ----------------------------------------------------------------

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!task.trim()) return;
    setSubmitting(true);
    setError(null);
    const result = await createBacklogTicket({
      task: task.trim(),
      project,
      priority: "normal",
      flags: [],
    });
    setSubmitting(false);
    if (result.error) {
      setError(result.error);
    } else {
      setTask("");
      load();
    }
  }

  async function handleDispatch(id: string) {
    const result = await dispatchBacklogTicket(id);
    if (result.error) setError(result.error);
    else load();
  }

  async function handleStatusChange(id: string, newStatus: string) {
    const result = await updateBacklogTicket(id, { status: newStatus });
    if (result.error) setError(result.error);
    else load();
  }


  // -- Ticket sorting per column ----------------------------------------------

  function ticketsForColumn(col: ColumnDef): BacklogTicket[] {
    return tickets
      .filter((t) => col.statuses.includes(t.status))
      .sort((a, b) => {
        const pa = PRIORITY_ORDER[a.priority] ?? 99;
        const pb = PRIORITY_ORDER[b.priority] ?? 99;
        if (pa !== pb) return pa - pb;
        const ta = typeof a.created_at === "number" ? a.created_at : new Date(a.created_at).getTime() / 1000;
        const tb = typeof b.created_at === "number" ? b.created_at : new Date(b.created_at).getTime() / 1000;
        return ta - tb;
      });
  }

  // -- Render -----------------------------------------------------------------

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-gray-500">
        <div className="h-5 w-5 border-2 border-gray-600 border-t-accent-green rounded-full animate-spin mr-3" />
        Loading backlog...
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full gap-4">
      {/* Error banner */}
      {error && (
        <div className="bg-accent-red/10 border border-accent-red/30 rounded-lg px-4 py-2 text-sm text-accent-red shrink-0">
          {error}
        </div>
      )}

      {/* Self-improvement ratio indicator */}
      {selfImprovement && (
        <div
          className={`flex items-center gap-3 px-4 py-2 rounded-lg text-xs shrink-0 ${
            selfImprovement.self_improvement_due
              ? "bg-accent-yellow/10 border border-accent-yellow/30 text-accent-yellow"
              : "bg-bg-surface border border-gray-800 text-gray-500"
          }`}
        >
          <span className="mono font-semibold">
            {selfImprovement.product_dispatches_since_last_self_improvement}/8
          </span>
          <span>
            {selfImprovement.self_improvement_due
              ? "Factory self-improvement due — next dispatch must be a dispatch-factory ticket"
              : "product dispatches until factory self-improvement"}
          </span>
          <span className="ml-auto text-[10px] text-gray-600 mono">
            factory: {selfImprovement.total_self_improvement_dispatches} | product: {selfImprovement.total_product_dispatches}
          </span>
        </div>
      )}

      {/* Quick-add bar */}
      <form onSubmit={handleCreate} className="flex flex-col gap-1.5 shrink-0">
        <div className="flex items-center gap-3">
          <input
            type="text"
            value={task}
            onChange={(e) => setTask(e.target.value.slice(0, 500))}
            placeholder={`Quick add task (min ${TASK_MIN_LENGTH} chars, be specific)...`}
            className="flex-1 bg-bg-surface border border-gray-700 rounded-lg px-4 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue/50"
          />
          <select
            value={project}
            onChange={(e) => setProject(e.target.value)}
            className="bg-bg-surface border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 mono focus:outline-none focus:border-accent-blue w-44"
          >
            {projects.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <button
            type="submit"
            disabled={submitting || !task.trim() || !!validateTaskQuality(task)}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-accent-blue/20 text-accent-blue border border-accent-blue/30 hover:bg-accent-blue/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
          >
            {submitting ? "Adding..." : "Add"}
          </button>
        </div>
        {task.trim() && validateTaskQuality(task) && (
          <div className="text-[10px] text-accent-yellow px-1">
            {validateTaskQuality(task)}
          </div>
        )}
      </form>

      {/* Kanban board */}
      <div className="flex gap-4 flex-1 min-h-0 overflow-x-auto pb-2">
        {COLUMNS.map((col) => {
          const colTickets = ticketsForColumn(col);
          const isCollapsed = col.collapsible && !doneExpanded;

          return (
            <div
              key={col.id}
              className={`flex flex-col min-w-[260px] flex-1 rounded-lg border-t-2 ${col.borderColor} bg-bg-surface/50`}
            >
              {/* Column header */}
              <div className="flex items-center justify-between px-3 py-2 shrink-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                    {col.title}
                  </span>
                  <span className="text-[10px] text-gray-600 mono">
                    {colTickets.length}
                  </span>
                </div>
                {col.collapsible && (
                  <button
                    onClick={() => setDoneExpanded((p) => !p)}
                    className="text-[10px] text-gray-600 hover:text-gray-400 transition-colors"
                  >
                    {doneExpanded ? "collapse" : "expand"}
                  </button>
                )}
                {col.pulse && colTickets.length > 0 && (
                  <div className="h-2 w-2 rounded-full bg-accent-cyan animate-pulse" />
                )}
              </div>

              {/* Cards container */}
              <div
                className="flex-1 overflow-y-auto px-2 pb-2 space-y-2 rounded-b-lg"
              >
                {isCollapsed ? (
                  <div className="text-center py-4 text-xs text-gray-600">
                    {colTickets.length} ticket{colTickets.length !== 1 ? "s" : ""}
                  </div>
                ) : (
                  colTickets.map((ticket) => (
                    <KanbanCard
                      key={ticket.id}
                      ticket={ticket}
                      columnId={col.id}
                      onDispatch={handleDispatch}
                      onRetry={(id) => handleStatusChange(id, "ready")}
                      onSelectSession={onSelectSession}
                      onUpdateTicket={async (id, updates) => {
                        await updateBacklogTicket(id, updates);
                        load();
                      }}
                      onOpenDetail={(t) => { setSelectedTicket(t); setNoteText(""); }}
                    />
                  ))
                )}
                {!isCollapsed && colTickets.length === 0 && (
                  <div className="text-center py-6 text-[11px] text-gray-700">
                    No tickets
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Ticket detail slide-over */}
      {selectedTicket && (
        <>
          <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" onClick={() => setSelectedTicket(null)} />
          <div className="fixed top-0 right-0 z-50 h-full w-[560px] max-w-full bg-bg-base border-l border-gray-800 flex flex-col shadow-2xl animate-slide-in">
            {/* Header */}
            <div className="shrink-0 border-b border-gray-800 px-5 py-4">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="inline-block h-3 w-3 rounded-full shrink-0" style={{ backgroundColor: projectColor(selectedTicket.project) }} />
                  <span className="text-xs mono text-gray-400">{selectedTicket.project}</span>
                  <span className={`text-xs font-semibold mono uppercase ${PRIORITY_COLORS[selectedTicket.priority] ?? "text-gray-500"}`}>{selectedTicket.priority}</span>
                  <span className="text-[10px] mono text-gray-600 px-1.5 py-0.5 rounded bg-bg-surface-alt">{selectedTicket.status}</span>
                </div>
                <button onClick={() => setSelectedTicket(null)} className="text-gray-500 hover:text-gray-300 transition-colors">
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
                </button>
              </div>
              <p className="text-sm text-gray-200 leading-relaxed">{selectedTicket.task}</p>
              {selectedTicket.session_id && (
                <button onClick={() => { setSelectedTicket(null); onSelectSession(selectedTicket.session_id!); }} className="mt-2 text-[10px] mono text-accent-cyan hover:text-accent-cyan/80">
                  Session: {selectedTicket.session_id}
                </button>
              )}
              <div className="text-[10px] text-gray-600 mt-1">ID: {selectedTicket.id} · Created: {formatTime(selectedTicket.created_at)}</div>
            </div>

            {/* Notes */}
            <div className="flex-1 overflow-y-auto px-5 py-4 space-y-2">
              {(!selectedTicket.notes || selectedTicket.notes.length === 0) && (
                <div className="text-center text-gray-600 text-sm py-8">No notes yet.</div>
              )}
              {selectedTicket.notes?.map((note, i) => (
                <div key={i} className={`rounded px-3 py-2 text-xs leading-relaxed ${
                  note.author === "human" ? "bg-accent-blue/10 border border-accent-blue/20" :
                  note.author === "foreman" ? "bg-accent-purple/10 border border-accent-purple/20" :
                  "bg-bg-surface-alt border border-gray-800"
                }`}>
                  <div className="flex items-center justify-between mb-1">
                    <span className={`text-[10px] font-semibold mono uppercase ${
                      note.author === "human" ? "text-accent-blue" : note.author === "foreman" ? "text-accent-purple" : "text-gray-500"
                    }`}>{note.author}</span>
                    <span className="text-[10px] text-gray-600">{formatTime(note.timestamp)}</span>
                  </div>
                  <p className="text-gray-300 whitespace-pre-wrap">{note.text}</p>
                </div>
              ))}
            </div>

            {/* Note input + actions */}
            <div className="shrink-0 border-t border-gray-800 px-5 py-3 space-y-2">
              <textarea
                value={noteText}
                onChange={(e) => setNoteText(e.target.value)}
                placeholder="Add a note or answer a question..."
                rows={2}
                className="w-full bg-bg-surface border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-accent-blue resize-none"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && noteText.trim()) {
                    e.preventDefault();
                    handleAddNote();
                  }
                  if (e.key === "Escape") setSelectedTicket(null);
                }}
              />
              <div className="flex gap-2 flex-wrap">
                <button
                  onClick={() => handleAddNote()}
                  disabled={noteSending || !noteText.trim()}
                  className="px-3 py-1.5 rounded text-xs font-medium bg-accent-blue/15 text-accent-blue border border-accent-blue/30 hover:bg-accent-blue/25 disabled:opacity-40 transition-colors"
                >
                  {noteSending ? "Sending..." : "Add Note"}
                </button>
                {(selectedTicket.status === "on_hold" || selectedTicket.status === "needs_input") && (
                  <button
                    onClick={() => handleAddNote("ready")}
                    disabled={noteSending || !noteText.trim()}
                    className="px-3 py-1.5 rounded text-xs font-medium bg-accent-green/15 text-accent-green border border-accent-green/30 hover:bg-accent-green/25 disabled:opacity-40 transition-colors"
                  >
                    Answer &amp; Ready
                  </button>
                )}
                {(selectedTicket.status === "ready" || selectedTicket.status === "pending") && (
                  <button
                    onClick={async () => { await dispatchBacklogTicket(selectedTicket.id); setSelectedTicket(null); load(); }}
                    className="px-3 py-1.5 rounded text-xs font-medium bg-accent-green/15 text-accent-green border border-accent-green/30 hover:bg-accent-green/25 transition-colors"
                  >
                    Dispatch
                  </button>
                )}
                <button
                  onClick={async () => { await updateBacklogTicket(selectedTicket.id, { status: "cancelled" }); setSelectedTicket(null); load(); }}
                  className="px-3 py-1.5 rounded text-xs text-accent-red/70 hover:text-accent-red transition-colors ml-auto"
                >
                  Cancel Ticket
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );

  async function handleAddNote(moveStatus?: string) {
    if (!selectedTicket || !noteText.trim()) return;
    setNoteSending(true);
    const result = await addTicketNote(selectedTicket.id, noteText.trim(), "human", moveStatus);
    setNoteSending(false);
    if (result.data) {
      setSelectedTicket(result.data);
      setNoteText("");
      load();
    }
  }
}

// -- KanbanCard ---------------------------------------------------------------

interface KanbanCardProps {
  ticket: BacklogTicket;
  columnId: string;
  onDispatch: (id: string) => void;
  onRetry: (id: string) => void;
  onSelectSession: (sessionId: string) => void;
  onUpdateTicket: (id: string, updates: Record<string, unknown>) => void;
  onOpenDetail: (ticket: BacklogTicket) => void;
}

function KanbanCard({
  ticket,
  columnId,
  onDispatch,
  onRetry,
  onSelectSession,
  onUpdateTicket,
  onOpenDetail,
}: KanbanCardProps) {
  const [editing, setEditing] = useState(false);
  const [editTask, setEditTask] = useState(ticket.task);
  const priorityColor = PRIORITY_COLORS[ticket.priority] ?? "text-gray-500";
  const cardBorder = columnId === "done" ? statusBorderColor(ticket.status) : "border-l-transparent";
  const bgClass = PRIORITY_BG[ticket.priority] ?? "bg-bg-surface border-gray-700";

  return (
    <div
      onClick={() => onOpenDetail(ticket)}
      className={`rounded-md border border-l-2 ${bgClass} ${cardBorder} px-3 py-2 cursor-pointer hover:bg-bg-surface-alt/50`}
    >
      {/* Task text (2 lines max) */}
      <p className="text-xs text-gray-300 leading-snug line-clamp-2 mb-1.5">
        {ticket.task}
      </p>

      {/* Metadata row */}
      <div className="flex items-center gap-2 flex-wrap">
        {/* Project */}
        <span className="flex items-center gap-1 text-[10px] text-gray-500">
          <span
            className="inline-block h-1.5 w-1.5 rounded-full shrink-0"
            style={{ backgroundColor: projectColor(ticket.project) }}
          />
          <span className="mono">{ticket.project}</span>
        </span>

        {/* Priority badge */}
        <span className={`text-[10px] font-semibold mono uppercase ${priorityColor}`}>
          {ticket.priority}
        </span>

        {/* Source badge */}
        {ticket.source && ticket.source !== "manual" && (
          <span
            className={`text-[10px] mono px-1 rounded ${
              ticket.source === "operator"
                ? "bg-accent-purple/15 text-accent-purple"
                : "bg-accent-cyan/15 text-accent-cyan"
            }`}
          >
            {ticket.source}
          </span>
        )}

        {/* Needs Input badge */}
        {ticket.status === "needs_input" && (
          <span className="text-[10px] mono px-1 rounded bg-accent-yellow/15 text-accent-yellow">
            needs input
          </span>
        )}
      </div>

      {/* Inline edit for needs_input / intake tickets */}
      {columnId === "intake" && !editing && (
        <button
          onClick={() => { setEditing(true); setEditTask(ticket.task); }}
          className="mt-2 w-full text-left px-2 py-1.5 rounded text-[10px] bg-accent-yellow/10 text-accent-yellow border border-accent-yellow/20 hover:bg-accent-yellow/15 transition-colors"
        >
          Edit &amp; move to Ready
        </button>
      )}
      {columnId === "intake" && editing && (
        <div className="mt-2 space-y-1.5">
          <textarea
            value={editTask}
            onChange={(e) => setEditTask(e.target.value)}
            rows={3}
            className="w-full bg-bg-base border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-accent-yellow resize-none"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && editTask.trim()) {
                e.preventDefault();
                onUpdateTicket(ticket.id, { task: editTask.trim(), status: "ready" });
                setEditing(false);
              }
              if (e.key === "Escape") setEditing(false);
            }}
          />
          <div className="flex gap-1.5">
            <button
              onClick={() => {
                onUpdateTicket(ticket.id, { task: editTask.trim(), status: "ready" });
                setEditing(false);
              }}
              disabled={!editTask.trim()}
              className="px-2 py-0.5 rounded text-[10px] font-medium bg-accent-green/15 text-accent-green border border-accent-green/30 hover:bg-accent-green/25 disabled:opacity-40"
            >
              Save &amp; Ready
            </button>
            <button
              onClick={() => setEditing(false)}
              className="px-2 py-0.5 rounded text-[10px] text-gray-500 hover:text-gray-300"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Session ID (In Flight) */}
      {columnId === "inflight" && ticket.session_id && (
        <button
          onClick={() => onSelectSession(ticket.session_id!)}
          className="mt-1.5 text-[10px] mono text-accent-cyan hover:text-accent-cyan/80 transition-colors truncate block max-w-full text-left"
        >
          {ticket.session_id}
        </button>
      )}

      {/* Actions row */}
      <div className="flex items-center justify-between mt-1.5">
        <span className="text-[10px] text-gray-600">{formatTime(ticket.created_at)}</span>

        <div className="flex gap-1.5">
          {/* Dispatch button (Ready column) */}
          {columnId === "ready" && (
            <button
              onClick={() => onDispatch(ticket.id)}
              className="px-2 py-0.5 rounded text-[10px] font-medium bg-accent-green/15 text-accent-green border border-accent-green/30 hover:bg-accent-green/25 transition-colors"
            >
              Dispatch
            </button>
          )}

          {/* Retry button (Done column, failed tickets) */}
          {columnId === "done" && ticket.status === "failed" && (
            <button
              onClick={() => onRetry(ticket.id)}
              className="px-2 py-0.5 rounded text-[10px] font-medium bg-accent-yellow/15 text-accent-yellow border border-accent-yellow/30 hover:bg-accent-yellow/25 transition-colors"
            >
              Retry
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
