import { useEffect, useState, useCallback } from "react";
import type { SessionSummary, ActiveSession } from "@/types";
import {
  fetchSessions,
  fetchActiveSessions,
  holdSession,
  attachTerminal,
} from "@/lib/api";

const STATE_LABELS: Record<string, { label: string; color: string }> = {
  running: { label: "Running", color: "bg-accent-green" },
  planning: { label: "Planning", color: "bg-accent-blue" },
  reviewing: { label: "Reviewing", color: "bg-accent-cyan" },
  verifying: { label: "Verifying", color: "bg-accent-yellow" },
  monitoring: { label: "Monitoring", color: "bg-accent-purple" },
  completed: { label: "Completed", color: "bg-gray-500" },
  deployed: { label: "Deployed", color: "bg-accent-green" },
  rolled_back: { label: "Rolled Back", color: "bg-accent-red" },
  error: { label: "Error", color: "bg-accent-red" },
};

const PROJECT_COLORS: Record<string, string> = {
  recipebrain: "#10b981",
  schoolbrain: "#3b82f6",
  movies: "#8b5cf6",
  lawpass: "#f59e0b",
  electricapp: "#06b6d4",
  "voice-bridge": "#ef4444",
};

const PIPELINE_STAGES = [
  "planner",
  "reviewer",
  "verifier",
  "monitor",
  "result",
];

function StageProgress({ artifactTypes }: { artifactTypes: string[] }) {
  return (
    <div className="flex gap-1 items-center">
      {PIPELINE_STAGES.map((stage, i) => {
        const completed = artifactTypes.includes(stage);
        return (
          <div key={stage} className="flex items-center gap-1">
            <div
              className={`h-2 w-2 rounded-full ${completed ? "bg-accent-green" : "bg-gray-700"}`}
              title={stage}
            />
            {i < PIPELINE_STAGES.length - 1 && (
              <div
                className={`h-px w-3 ${completed ? "bg-accent-green/50" : "bg-gray-800"}`}
              />
            )}
          </div>
        );
      })}
      <span className="ml-2 text-[10px] text-gray-600 mono">
        {PIPELINE_STAGES.filter((s) => artifactTypes.includes(s)).length}/
        {PIPELINE_STAGES.length}
      </span>
    </div>
  );
}

interface PipelineListProps {
  onAttachTerminal: (sessionName: string, port: number) => void;
}

export default function PipelineList({ onAttachTerminal }: PipelineListProps) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeTmux, setActiveTmux] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const [sessResult, activeResult] = await Promise.all([
      fetchSessions(),
      fetchActiveSessions(),
    ]);
    if (sessResult.error) {
      setError(sessResult.error);
    } else if (sessResult.data) {
      setSessions(sessResult.data);
      setError(null);
    }
    if (activeResult.data) {
      setActiveTmux(
        new Set(activeResult.data.map((a: ActiveSession) => a.id))
      );
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [load]);

  // Split into live (has tmux session or running state) and historical
  const live = sessions.filter(
    (s) =>
      activeTmux.has(s.id) ||
      ["running", "planning", "reviewing", "verifying", "monitoring"].includes(
        s.state
      )
  );
  const recent = sessions
    .filter((s) => !live.includes(s))
    .slice(0, 30);

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
      onAttachTerminal(result.data.session, result.data.port);
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
          Active ({live.length})
        </h2>
        {live.length === 0 ? (
          <div className="bg-bg-surface rounded-lg border border-gray-800 p-8 text-center text-gray-500">
            No active sessions. Create a ticket to start.
          </div>
        ) : (
          <div className="grid gap-3">
            {live.map((session) => {
              const sl = STATE_LABELS[session.state] ?? {
                label: session.state,
                color: "bg-gray-600",
              };
              const inTmux = activeTmux.has(session.id);
              return (
                <div
                  key={session.id}
                  className="bg-bg-surface rounded-lg border border-gray-800 p-4 hover:border-gray-700 transition-colors"
                >
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <div
                        className="h-3 w-3 rounded-full shrink-0"
                        style={{
                          backgroundColor:
                            PROJECT_COLORS[session.project] ?? "#6b7280",
                        }}
                      />
                      <span className="mono text-sm font-medium text-gray-200">
                        {session.project}
                      </span>
                      <span className="mono text-xs text-gray-600">
                        {session.id}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      {inTmux && (
                        <span className="text-[10px] mono text-accent-green bg-accent-green/10 px-1.5 py-0.5 rounded">
                          tmux
                        </span>
                      )}
                      <span
                        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium`}
                      >
                        <span
                          className={`h-1.5 w-1.5 rounded-full ${sl.color} ${
                            session.state === "running" ? "animate-pulse" : ""
                          }`}
                        />
                        {sl.label}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center justify-between">
                    <StageProgress artifactTypes={session.artifact_types} />
                    <span className="text-[10px] mono text-gray-600">
                      {session.type}
                    </span>
                  </div>

                  {inTmux && (
                    <div className="flex gap-2 mt-3 pt-3 border-t border-gray-800">
                      <button
                        onClick={() => handleAttach(session.id)}
                        className="px-3 py-1.5 text-xs mono bg-accent-green/10 hover:bg-accent-green/20 text-accent-green border border-accent-green/30 rounded transition-colors"
                      >
                        Attach Terminal
                      </button>
                      <button
                        onClick={() => handleHold(session.id)}
                        className="px-3 py-1.5 text-xs mono bg-bg-surface-alt hover:bg-gray-700 border border-gray-700 rounded transition-colors"
                      >
                        Hold
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Recent Sessions */}
      {recent.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
            Recent ({recent.length})
          </h2>
          <div className="grid gap-1">
            {recent.map((session) => {
              const sl = STATE_LABELS[session.state] ?? {
                label: session.state,
                color: "bg-gray-600",
              };
              return (
                <div
                  key={session.id}
                  className="bg-bg-surface/50 rounded border border-gray-800/50 px-3 py-2 flex items-center justify-between group hover:border-gray-700 transition-colors"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div
                      className="h-2 w-2 rounded-full shrink-0"
                      style={{
                        backgroundColor:
                          PROJECT_COLORS[session.project] ?? "#6b7280",
                      }}
                    />
                    <span className="mono text-xs text-gray-500 shrink-0">
                      {session.project}
                    </span>
                    <span className="mono text-[11px] text-gray-600 truncate">
                      {session.id}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <StageProgress artifactTypes={session.artifact_types} />
                    <span
                      className={`text-xs ${
                        session.state === "deployed"
                          ? "text-accent-green"
                          : session.state === "error" ||
                              session.state === "rolled_back"
                            ? "text-accent-red"
                            : "text-gray-500"
                      }`}
                    >
                      {sl.label}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
