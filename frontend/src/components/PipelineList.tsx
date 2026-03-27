import { useState, useEffect, useCallback } from "react";
import type { SessionSummary, ActiveSession } from "@/types";
import {
  fetchSessions,
  fetchActiveSessions,
  holdSession,
  attachTerminal,
  fetchSessionOutput,
} from "@/lib/api";
import { useFactorySocket } from "@/lib/useFactorySocket";
import { useNotifications } from "@/lib/useNotifications";

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
  abandoned: { label: "Abandoned", color: "bg-accent-yellow" },
};

// Generate a consistent color from project name
const PALETTE = ["#10b981", "#3b82f6", "#8b5cf6", "#f59e0b", "#06b6d4", "#ef4444", "#ec4899", "#f97316"];
function projectColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

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

function LiveOutput({ sessionId }: { sessionId: string }) {
  const [lines, setLines] = useState<string[]>([]);

  useEffect(() => {
    let mounted = true;
    const poll = async () => {
      const result = await fetchSessionOutput(sessionId, 15);
      if (mounted && result.data) {
        setLines(result.data.lines.filter((l) => l.trim()));
      }
    };
    poll();
    const interval = setInterval(poll, 3000);
    return () => { mounted = false; clearInterval(interval); };
  }, [sessionId]);

  if (lines.length === 0) return null;

  return (
    <div className="mt-3 bg-bg-base rounded border border-gray-800 p-2 max-h-48 overflow-y-auto">
      <pre className="text-[11px] mono text-gray-400 leading-relaxed whitespace-pre-wrap">
        {lines.join("\n")}
      </pre>
    </div>
  );
}

interface PipelineListProps {
  onAttachTerminal: (sessionName: string, port: number) => void;
  onSelectSession: (sessionId: string) => void;
}

export default function PipelineList({ onAttachTerminal, onSelectSession }: PipelineListProps) {
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

  // Real-time updates via WebSocket, falls back to 5s polling
  useFactorySocket(load);

  // Browser notifications when workers start/finish
  useNotifications(sessions);

  // Only sessions with a live tmux process are truly active
  const live = sessions.filter((s) => activeTmux.has(s.id));
  const recent = sessions
    .filter((s) => !activeTmux.has(s.id))
    .slice(0, 15);

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
                            projectColor(session.project),
                        }}
                      />
                      <span className="mono text-sm font-medium text-gray-200">
                        {session.project}
                      </span>
                      <button
                        onClick={() => onSelectSession(session.id)}
                        className="mono text-xs text-gray-600 hover:text-accent-cyan transition-colors"
                      >
                        {session.id}
                      </button>
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

                  {session.task && (
                    <p className="text-sm text-gray-400 mb-3">
                      {session.task}
                    </p>
                  )}

                  <div className="flex items-center justify-between">
                    <StageProgress artifactTypes={session.artifact_types} />
                    <span className="text-[10px] mono text-gray-600">
                      {session.type}
                    </span>
                  </div>

                  {inTmux && <LiveOutput sessionId={session.id} />}

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

      {/* Recent Sessions — disabled for now, too noisy */}
      {false && recent.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
            Recent
          </h2>
          <div className="bg-bg-surface rounded-lg border border-gray-800 overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[10px] text-gray-600 uppercase tracking-wider border-b border-gray-800">
                  <th className="text-left py-2 px-3 w-8"></th>
                  <th className="text-left py-2 px-3">Session</th>
                  <th className="text-left py-2 px-3">Pipeline</th>
                  <th className="text-right py-2 px-3">State</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((session) => {
                  const sl = STATE_LABELS[session.state] ?? {
                    label: session.state,
                    color: "bg-gray-600",
                  };
                  return (
                    <tr
                      key={session.id}
                      className="border-b border-gray-800/30 hover:bg-bg-surface-alt/30 transition-colors"
                    >
                      <td className="py-1.5 px-3">
                        <div
                          className="h-2 w-2 rounded-full"
                          style={{
                            backgroundColor:
                              projectColor(session.project),
                          }}
                        />
                      </td>
                      <td className="py-1.5 px-3 mono text-gray-500">
                        {session.id}
                      </td>
                      <td className="py-1.5 px-3">
                        <StageProgress
                          artifactTypes={session.artifact_types}
                        />
                      </td>
                      <td className="py-1.5 px-3 text-right">
                        <span
                          className={`${
                            session.state === "deployed"
                              ? "text-accent-green"
                              : session.state === "error" ||
                                  session.state === "rolled_back"
                                ? "text-accent-red"
                                : "text-gray-600"
                          }`}
                        >
                          {sl.label}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
