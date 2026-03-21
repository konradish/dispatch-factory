import { useEffect, useState } from "react";
import type { HistorySession, Brief } from "@/types";
import { fetchHistory, fetchBrief } from "@/lib/api";

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

const PIPELINE_STAGES = ["planner", "reviewer", "verifier", "monitor", "result"];

function StageProgress({ artifactTypes }: { artifactTypes: string[] }) {
  return (
    <div className="flex gap-0.5 items-center">
      {PIPELINE_STAGES.map((stage, i) => {
        const completed = artifactTypes.includes(stage);
        return (
          <div key={stage} className="flex items-center gap-0.5">
            <div
              className={`h-1.5 w-1.5 rounded-full ${completed ? "bg-accent-green" : "bg-gray-700"}`}
              title={stage}
            />
            {i < PIPELINE_STAGES.length - 1 && (
              <div
                className={`h-px w-2 ${completed ? "bg-accent-green/50" : "bg-gray-800"}`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

function relativeTime(mtime: number): string {
  const diff = Math.floor(Date.now() / 1000 - mtime);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return `${Math.floor(diff / 604800)}w ago`;
}

function ResultBadge({ session }: { session: HistorySession }) {
  const { state, summary } = session;
  if (summary.healed) {
    return (
      <span className="text-accent-yellow" title="Healed">
        &#9881;
      </span>
    );
  }
  if (state === "deployed") {
    return (
      <span className="text-accent-green" title="Deployed">
        &#10003;
      </span>
    );
  }
  if (state === "error" || state === "rolled_back") {
    return (
      <span className="text-accent-red" title="Error">
        &#10007;
      </span>
    );
  }
  if (state === "completed") {
    return (
      <span className="text-gray-500" title="Completed">
        &#10003;
      </span>
    );
  }
  return <span className="text-gray-700">-</span>;
}

function BriefCard({ brief }: { brief: Brief }) {
  const { stats, projects, direction } = brief;
  return (
    <div className="bg-bg-surface rounded-lg border border-gray-800 p-4 mb-4">
      <div className="flex items-center gap-6 text-xs mb-2">
        <span className="text-gray-400">
          <span className="mono text-gray-200">{stats.total_sessions}</span>{" "}
          total
        </span>
        <span className="text-accent-green">
          <span className="mono">{stats.deployed}</span> deployed
        </span>
        <span className="text-accent-red">
          <span className="mono">{stats.failed}</span> failed
        </span>
        <span className="text-accent-yellow">
          <span className="mono">{stats.healed}</span> healed
        </span>
        <span className="text-gray-400">
          <span className="mono text-gray-200">
            {Math.round(stats.success_rate * 100)}%
          </span>{" "}
          success
        </span>
      </div>
      {direction && (
        <p className="text-xs text-gray-500 italic mb-2">{direction}</p>
      )}
      <div className="flex gap-2 flex-wrap">
        {Object.entries(projects).map(([name, p]) => (
          <span
            key={name}
            className="inline-flex items-center gap-1.5 text-[10px] mono bg-bg-surface-alt px-2 py-0.5 rounded"
          >
            <span
              className="h-1.5 w-1.5 rounded-full inline-block"
              style={{ backgroundColor: projectColor(name) }}
            />
            {name}
            <span className="text-accent-green">{p.deployed}</span>
            <span className="text-gray-600">/</span>
            <span className="text-gray-400">{p.total}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

export default function HistoryView() {
  const [sessions, setSessions] = useState<HistorySession[]>([]);
  const [brief, setBrief] = useState<Brief | null>(null);

  useEffect(() => {
    fetchHistory().then((r) => {
      if (r.data) setSessions(r.data);
    });
    fetchBrief().then((r) => {
      if (r.data) setBrief(r.data);
    });
  }, []);

  return (
    <div>
      {brief && <BriefCard brief={brief} />}

      {sessions.length === 0 ? (
        <div className="text-gray-600 text-sm py-8 text-center">
          No history yet.
        </div>
      ) : (
        <div className="bg-bg-surface rounded-lg border border-gray-800 overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-[10px] text-gray-600 uppercase tracking-wider border-b border-gray-800">
                <th className="text-left py-2 px-3">Time</th>
                <th className="text-left py-2 px-3">Project</th>
                <th className="text-left py-2 px-3">Task</th>
                <th className="text-center py-2 px-2">Result</th>
                <th className="text-left py-2 px-3">Pipeline</th>
                <th className="text-left py-2 px-2">Verdict</th>
                <th className="text-left py-2 px-2">Deploy</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => (
                <tr
                  key={s.id}
                  className="border-b border-gray-800/30 hover:bg-bg-surface-alt/30 transition-colors h-9"
                >
                  <td className="py-1 px-3 mono text-gray-500 whitespace-nowrap">
                    {relativeTime(s.mtime)}
                  </td>
                  <td className="py-1 px-3 whitespace-nowrap">
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        className="h-2 w-2 rounded-full inline-block shrink-0"
                        style={{ backgroundColor: projectColor(s.project) }}
                      />
                      <span className="text-gray-300">{s.project}</span>
                    </span>
                  </td>
                  <td className="py-1 px-3 text-gray-400 max-w-xs truncate">
                    {s.task.length > 60
                      ? s.task.slice(0, 60) + "\u2026"
                      : s.task}
                  </td>
                  <td className="py-1 px-2 text-center">
                    <ResultBadge session={s} />
                  </td>
                  <td className="py-1 px-3">
                    <StageProgress artifactTypes={s.artifact_types} />
                  </td>
                  <td className="py-1 px-2 mono whitespace-nowrap">
                    {s.summary.verdict === "APPROVE" && (
                      <span className="text-accent-green">APPROVE</span>
                    )}
                    {s.summary.verdict === "REQUEST_CHANGES" && (
                      <span className="text-accent-yellow">CHANGES</span>
                    )}
                  </td>
                  <td className="py-1 px-2 mono whitespace-nowrap">
                    {s.summary.deploy_status === "DEPLOYED" && (
                      <span className="text-accent-green">DEPLOYED</span>
                    )}
                    {s.summary.deploy_status === "FAILED" && (
                      <span className="text-accent-red">FAILED</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
