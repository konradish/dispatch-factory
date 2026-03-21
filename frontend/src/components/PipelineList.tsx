import { useEffect, useState, useCallback } from "react";
import type { Session } from "@/types";
import { fetchSessions, holdSession, attachTerminal } from "@/lib/api";

const STATION_ORDER = [
  "Planner",
  "Worker",
  "Reviewer",
  "Verifier",
  "Monitor",
  "Reporter",
];

const STATUS_COLORS: Record<string, string> = {
  processing: "bg-accent-green",
  healing: "bg-accent-yellow",
  queued: "bg-accent-blue",
  held: "bg-accent-purple",
  completed: "bg-accent-green",
  failed: "bg-accent-red",
};

const PROJECT_COLORS: Record<string, string> = {
  recipebrain: "#10b981",
  schoolbrain: "#3b82f6",
  movies: "#8b5cf6",
  lawpass: "#f59e0b",
  electricapp: "#06b6d4",
  "voice-bridge": "#ef4444",
};

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m > 60) {
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }
  return `${m}m ${s}s`;
}

function StationProgress({ current }: { current: string }) {
  const idx = STATION_ORDER.indexOf(current);
  return (
    <div className="flex gap-1 items-center">
      {STATION_ORDER.map((station, i) => (
        <div key={station} className="flex items-center gap-1">
          <div
            className={`h-2 w-2 rounded-full ${
              i < idx
                ? "bg-accent-green"
                : i === idx
                  ? "bg-accent-green animate-pulse"
                  : "bg-gray-600"
            }`}
            title={station}
          />
          {i < STATION_ORDER.length - 1 && (
            <div
              className={`h-px w-3 ${i < idx ? "bg-accent-green" : "bg-gray-700"}`}
            />
          )}
        </div>
      ))}
      <span className="ml-2 text-xs text-gray-400 mono">{current}</span>
    </div>
  );
}

interface PipelineListProps {
  onAttachTerminal: (sessionName: string, port: number) => void;
}

export default function PipelineList({ onAttachTerminal }: PipelineListProps) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const result = await fetchSessions();
    if (result.error) {
      setError(result.error);
    } else if (result.data) {
      setSessions(result.data);
      setError(null);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [load]);

  const active = sessions.filter(
    (s) => s.status !== "completed" && s.status !== "failed"
  );
  const recent = sessions.filter(
    (s) => s.status === "completed" || s.status === "failed"
  );

  async function handleHold(id: string) {
    const result = await holdSession(id);
    if (result.error) {
      setError(result.error);
    } else {
      load();
    }
  }

  async function handleAttach(sessionName: string) {
    const result = await attachTerminal(sessionName);
    if (result.error) {
      setError(result.error);
    } else if (result.data) {
      onAttachTerminal(result.data.session_name, result.data.port);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-gray-500">
        <div className="h-5 w-5 border-2 border-gray-600 border-t-accent-green rounded-full animate-spin mr-3" />
        Loading sessions...
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="bg-accent-red/10 border border-accent-red/30 rounded-lg px-4 py-3 text-sm text-accent-red">
          {error}
        </div>
      )}

      {/* Active Sessions */}
      <div>
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
          Active Sessions
        </h2>
        {active.length === 0 ? (
          <div className="bg-bg-surface rounded-lg border border-gray-800 p-8 text-center text-gray-500">
            No active sessions. Create a ticket to start.
          </div>
        ) : (
          <div className="grid gap-3">
            {active.map((session) => (
              <div
                key={session.id}
                className="bg-bg-surface rounded-lg border border-gray-800 p-4 hover:border-gray-700 transition-colors"
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <div
                      className="h-3 w-3 rounded-full"
                      style={{
                        backgroundColor:
                          PROJECT_COLORS[session.project] ?? "#6b7280",
                      }}
                    />
                    <span className="mono text-sm font-medium">
                      {session.project}
                    </span>
                    <span
                      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[session.status] ?? "bg-gray-600"}/20 text-gray-200`}
                    >
                      <span
                        className={`h-1.5 w-1.5 rounded-full ${STATUS_COLORS[session.status] ?? "bg-gray-600"}`}
                      />
                      {session.status}
                    </span>
                  </div>
                  <span className="mono text-xs text-gray-500">
                    {formatElapsed(session.elapsed_seconds)}
                  </span>
                </div>

                <p className="text-sm text-gray-300 mb-3 line-clamp-2">
                  {session.task}
                </p>

                <StationProgress current={session.station} />

                <div className="flex gap-2 mt-3 pt-3 border-t border-gray-800">
                  <button
                    onClick={() => handleAttach(session.id)}
                    className="px-3 py-1.5 text-xs mono bg-bg-surface-alt hover:bg-gray-700 border border-gray-700 rounded transition-colors"
                  >
                    Attach Terminal
                  </button>
                  <button
                    onClick={() => handleHold(session.id)}
                    className="px-3 py-1.5 text-xs mono bg-bg-surface-alt hover:bg-gray-700 border border-gray-700 rounded transition-colors"
                    disabled={session.held}
                  >
                    {session.held ? "Held" : "Hold"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Recent Sessions */}
      {recent.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
            Recent (Last 24h)
          </h2>
          <div className="grid gap-2">
            {recent.map((session) => (
              <div
                key={session.id}
                className="bg-bg-surface/50 rounded-lg border border-gray-800/50 p-3 flex items-center justify-between"
              >
                <div className="flex items-center gap-3">
                  <div
                    className="h-2.5 w-2.5 rounded-full"
                    style={{
                      backgroundColor:
                        PROJECT_COLORS[session.project] ?? "#6b7280",
                    }}
                  />
                  <span className="mono text-xs text-gray-400">
                    {session.project}
                  </span>
                  <span className="text-sm text-gray-300 truncate max-w-md">
                    {session.task}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <span
                    className={`text-xs ${session.status === "completed" ? "text-accent-green" : "text-accent-red"}`}
                  >
                    {session.status}
                  </span>
                  <span className="mono text-xs text-gray-600">
                    {formatElapsed(session.elapsed_seconds)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
