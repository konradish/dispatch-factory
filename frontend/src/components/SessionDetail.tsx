import { useEffect, useState, useCallback } from "react";
import type { SessionDetail as SessionDetailType } from "@/types";
import { fetchSession } from "@/lib/api";

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

function extractProject(sessionId: string): string {
  // worker-electricapp-1824 -> electricapp
  // deploy-recipebrain-1430 -> recipebrain
  const parts = sessionId.split("-");
  if (parts.length >= 3) {
    return parts.slice(1, -1).join("-");
  }
  return sessionId;
}

function StateBadge({ state }: { state: string }) {
  const colors: Record<string, string> = {
    running: "bg-accent-green/15 text-accent-green",
    planning: "bg-accent-blue/15 text-accent-blue",
    reviewing: "bg-accent-cyan/15 text-accent-cyan",
    verifying: "bg-accent-yellow/15 text-accent-yellow",
    monitoring: "bg-accent-purple/15 text-accent-purple",
    completed: "bg-gray-500/15 text-gray-400",
    deployed: "bg-accent-green/15 text-accent-green",
    rolled_back: "bg-accent-red/15 text-accent-red",
    error: "bg-accent-red/15 text-accent-red",
  };
  const c = colors[state] ?? "bg-gray-500/15 text-gray-400";
  return (
    <span className={`${c} text-xs mono px-2 py-0.5 rounded`}>
      {state.toUpperCase().replace("_", " ")}
    </span>
  );
}

// --- Artifact renderers ---

function PlannerSection({ data }: { data: Record<string, unknown> }) {
  const steps = (data.steps as string[]) ?? [];
  const risks = (data.risks as string[]) ?? [];
  const files = (data.affected_files as string[]) ?? [];
  return (
    <div className="space-y-2">
      {!!data.scope && (
        <div>
          <span className="text-[10px] uppercase text-gray-600 tracking-wider">Scope</span>
          <p className="text-xs text-gray-300 mt-0.5">{String(data.scope)}</p>
        </div>
      )}
      {!!data.type && (
        <div>
          <span className="text-[10px] uppercase text-gray-600 tracking-wider">Type</span>
          <p className="text-xs text-gray-300 mono mt-0.5">{String(data.type)}</p>
        </div>
      )}
      {steps.length > 0 && (
        <div>
          <span className="text-[10px] uppercase text-gray-600 tracking-wider">Steps</span>
          <ol className="list-decimal list-inside text-xs text-gray-400 mt-0.5 space-y-0.5">
            {steps.map((s, i) => <li key={i}>{s}</li>)}
          </ol>
        </div>
      )}
      {risks.length > 0 && (
        <div>
          <span className="text-[10px] uppercase text-gray-600 tracking-wider">Risks</span>
          <ul className="list-disc list-inside text-xs text-accent-yellow/80 mt-0.5 space-y-0.5">
            {risks.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}
      {files.length > 0 && (
        <div>
          <span className="text-[10px] uppercase text-gray-600 tracking-wider">Affected Files</span>
          <div className="flex flex-wrap gap-1 mt-1">
            {files.map((f, i) => (
              <span key={i} className="mono text-[10px] bg-bg-surface-alt px-1.5 py-0.5 rounded text-gray-500">
                {f}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ReviewerSection({ data }: { data: Record<string, unknown> }) {
  const verdict = String(data.verdict ?? "");
  const feedback = String(data.feedback ?? "");
  const policyIssues = (data.policy_issues as string[]) ?? [];
  const unmetCriteria = (data.unmet_criteria as string[]) ?? [];
  const alreadySatisfied = (data.already_satisfied as string[]) ?? [];
  const verdictColor = verdict === "APPROVE" ? "text-accent-green" : "text-accent-yellow";
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase text-gray-600 tracking-wider">Verdict</span>
        <span className={`mono text-xs font-semibold ${verdictColor}`}>{verdict}</span>
      </div>
      {feedback && (
        <div>
          <span className="text-[10px] uppercase text-gray-600 tracking-wider">Feedback</span>
          <p className="text-xs text-gray-300 mt-0.5 whitespace-pre-wrap leading-relaxed">{feedback}</p>
        </div>
      )}
      {policyIssues.length > 0 && (
        <div>
          <span className="text-[10px] uppercase text-gray-600 tracking-wider">Policy Issues</span>
          <ul className="list-disc list-inside text-xs text-accent-red/80 mt-0.5 space-y-0.5">
            {policyIssues.map((p, i) => <li key={i}>{p}</li>)}
          </ul>
        </div>
      )}
      {unmetCriteria.length > 0 && (
        <div>
          <span className="text-[10px] uppercase text-gray-600 tracking-wider">Unmet Criteria</span>
          <ul className="list-disc list-inside text-xs text-accent-yellow/80 mt-0.5 space-y-0.5">
            {unmetCriteria.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      )}
      {alreadySatisfied.length > 0 && (
        <div>
          <span className="text-[10px] uppercase text-gray-600 tracking-wider">Already Satisfied</span>
          <ul className="list-disc list-inside text-xs text-gray-500 mt-0.5 space-y-0.5">
            {alreadySatisfied.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

function VerifierSection({ data }: { data: Record<string, unknown> }) {
  const status = String(data.status ?? "");
  const stages = (data.stages as Record<string, string>) ?? {};
  const reason = String(data.reason ?? "");
  const statusColor = status === "PASSED" || status === "SUCCESS"
    ? "text-accent-green"
    : "text-accent-red";
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase text-gray-600 tracking-wider">Status</span>
        <span className={`mono text-xs font-semibold ${statusColor}`}>{status}</span>
      </div>
      {Object.keys(stages).length > 0 && (
        <div>
          <span className="text-[10px] uppercase text-gray-600 tracking-wider">Stages</span>
          <table className="mt-1 text-xs">
            <tbody>
              {Object.entries(stages).map(([name, result]) => (
                <tr key={name}>
                  <td className="mono text-gray-500 pr-4 py-0.5">{name}</td>
                  <td className={`mono font-semibold py-0.5 ${
                    result === "PASS" || result === "FIXED" ? "text-accent-green"
                    : result === "FAIL" ? "text-accent-red"
                    : "text-gray-500"
                  }`}>{result}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {reason && (
        <div>
          <span className="text-[10px] uppercase text-gray-600 tracking-wider">Reason</span>
          <p className="text-xs text-gray-400 mt-0.5 whitespace-pre-wrap">{reason}</p>
        </div>
      )}
    </div>
  );
}

function HealerSection({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="space-y-2 bg-accent-yellow/5 -mx-3 -my-2 px-3 py-2 rounded">
      <div className="flex items-center gap-3">
        <div>
          <span className="text-[10px] uppercase text-gray-600 tracking-wider">Action</span>
          <p className="mono text-xs text-accent-yellow font-semibold">{String(data.action ?? "")}</p>
        </div>
        <div>
          <span className="text-[10px] uppercase text-gray-600 tracking-wider">Confidence</span>
          <p className="mono text-xs text-gray-300">{String(data.confidence ?? "")}</p>
        </div>
        {!!data.stage && (
          <div>
            <span className="text-[10px] uppercase text-gray-600 tracking-wider">Stage</span>
            <p className="mono text-xs text-gray-300">{String(data.stage)}</p>
          </div>
        )}
      </div>
      {!!data.diagnosis && (
        <div>
          <span className="text-[10px] uppercase text-gray-600 tracking-wider">Diagnosis</span>
          <p className="text-xs text-gray-300 mt-0.5 whitespace-pre-wrap leading-relaxed">{String(data.diagnosis)}</p>
        </div>
      )}
    </div>
  );
}

function MonitorSection({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="space-y-1">
      {Object.entries(data).map(([key, value]) => (
        <div key={key} className="flex gap-2">
          <span className="mono text-[10px] text-gray-600 shrink-0">{key}:</span>
          <span className="text-xs text-gray-400 whitespace-pre-wrap break-all">
            {typeof value === "object" ? JSON.stringify(value, null, 2) : String(value)}
          </span>
        </div>
      ))}
    </div>
  );
}

function renderSimpleMarkdown(text: string): string {
  return text
    // Headers
    .replace(/^### (.+)$/gm, '<h4 class="text-sm font-semibold text-gray-200 mt-3 mb-1">$1</h4>')
    .replace(/^## (.+)$/gm, '<h3 class="text-sm font-bold text-gray-100 mt-4 mb-1">$1</h3>')
    .replace(/^# (.+)$/gm, '<h2 class="text-base font-bold text-white mt-4 mb-2">$1</h2>')
    // Bold
    .replace(/\*\*(.+?)\*\*/g, '<strong class="text-gray-200 font-semibold">$1</strong>')
    // Links
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener" class="text-accent-cyan hover:underline">$1</a>')
    // Bare URLs
    .replace(/(^|[^"'])(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noopener" class="text-accent-cyan hover:underline">$2</a>')
    // List items
    .replace(/^  - (.+)$/gm, '<div class="pl-4 text-gray-400">• $1</div>')
    .replace(/^- (.+)$/gm, '<div class="text-gray-400">• $1</div>')
    // Line breaks
    .replace(/\n/g, '<br/>');
}

function ResultSection({ data }: { data: string }) {
  return (
    <div
      className="text-xs text-gray-300 leading-relaxed overflow-x-auto space-y-0.5"
      dangerouslySetInnerHTML={{ __html: renderSimpleMarkdown(data) }}
    />
  );
}

// --- Timeline stage config ---

const STAGE_CONFIG: {
  key: string;
  label: string;
  dotColor: string;
  render: (data: unknown) => React.ReactNode;
}[] = [
  {
    key: "planner",
    label: "Planner",
    dotColor: "bg-accent-blue",
    render: (data) => <PlannerSection data={data as Record<string, unknown>} />,
  },
  {
    key: "reviewer",
    label: "Reviewer",
    dotColor: "bg-accent-cyan",
    render: (data) => <ReviewerSection data={data as Record<string, unknown>} />,
  },
  {
    key: "verifier",
    label: "Verifier",
    dotColor: "bg-accent-yellow",
    render: (data) => <VerifierSection data={data as Record<string, unknown>} />,
  },
  {
    key: "healer",
    label: "Healer",
    dotColor: "bg-accent-yellow",
    render: (data) => <HealerSection data={data as Record<string, unknown>} />,
  },
  {
    key: "monitor",
    label: "Monitor",
    dotColor: "bg-accent-purple",
    render: (data) => <MonitorSection data={data as Record<string, unknown>} />,
  },
  {
    key: "result",
    label: "Result",
    dotColor: "bg-gray-500",
    render: (data) => <ResultSection data={String(data)} />,
  },
];

interface SessionDetailProps {
  sessionId: string;
  onClose: () => void;
}

export default function SessionDetail({ sessionId, onClose }: SessionDetailProps) {
  const [session, setSession] = useState<SessionDetailType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchSession(sessionId).then((r) => {
      if (r.error) {
        setError(r.error);
      } else if (r.data) {
        setSession(r.data);
      }
      setLoading(false);
    });
  }, [sessionId]);

  // Close on Escape
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose]
  );
  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  const project = extractProject(sessionId);
  const artifacts = session?.artifacts ?? {};

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Slide-over panel */}
      <div className="fixed top-0 right-0 z-50 h-full w-[600px] max-w-full bg-bg-base border-l border-gray-800 flex flex-col shadow-2xl animate-slide-in">
        {/* Header */}
        <div className="shrink-0 border-b border-gray-800 px-5 py-4">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-3">
              <div
                className="h-3 w-3 rounded-full shrink-0"
                style={{ backgroundColor: projectColor(project) }}
              />
              <span className="mono text-sm font-medium text-gray-200">{project}</span>
              {session && <StateBadge state={session.state} />}
            </div>
            <button
              onClick={onClose}
              className="text-gray-500 hover:text-gray-300 transition-colors p-1"
              title="Close (Esc)"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M18 6L6 18M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div className="mono text-xs text-gray-600">{sessionId}</div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading && (
            <div className="flex items-center justify-center py-12 text-gray-500">
              <div className="h-4 w-4 border-2 border-gray-600 border-t-accent-green rounded-full animate-spin mr-2" />
              <span className="text-sm">Loading session...</span>
            </div>
          )}

          {error && (
            <div className="bg-accent-red/10 border border-accent-red/30 rounded-lg px-4 py-3 text-sm text-accent-red">
              {error}
            </div>
          )}

          {session && !loading && (
            <div className="space-y-0">
              {/* Pipeline timeline */}
              {STAGE_CONFIG.filter((stage) => artifacts[stage.key] != null).length === 0 ? (
                <div className="text-gray-600 text-sm py-8 text-center">
                  No pipeline artifacts yet.
                </div>
              ) : (
                <div className="relative">
                  {/* Vertical line */}
                  <div className="absolute left-[7px] top-3 bottom-3 w-px bg-gray-800" />

                  {STAGE_CONFIG.filter((stage) => artifacts[stage.key] != null).map((stage, i, arr) => (
                    <div key={stage.key} className="relative pl-7 pb-5">
                      {/* Dot */}
                      <div
                        className={`absolute left-0 top-1 h-[15px] w-[15px] rounded-full ${stage.dotColor} border-2 border-bg-base`}
                      />

                      {/* Stage label */}
                      <div className="text-xs font-semibold text-gray-300 uppercase tracking-wider mb-2">
                        {stage.label}
                      </div>

                      {/* Stage content */}
                      <div className="bg-bg-surface rounded-lg border border-gray-800 px-3 py-2">
                        {stage.render(artifacts[stage.key])}
                      </div>

                      {/* Spacer for last item */}
                      {i === arr.length - 1 && <div className="h-1" />}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="shrink-0 border-t border-gray-800 px-5 py-3 flex gap-2">
          <button
            disabled
            className="px-3 py-1.5 text-xs mono bg-bg-surface-alt border border-gray-700 rounded text-gray-600 cursor-not-allowed"
          >
            View Log
          </button>
          <button
            disabled
            className="px-3 py-1.5 text-xs mono bg-bg-surface-alt border border-gray-700 rounded text-gray-600 cursor-not-allowed"
          >
            Re-dispatch
          </button>
        </div>
      </div>
    </>
  );
}
