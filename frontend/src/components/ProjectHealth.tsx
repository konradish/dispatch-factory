import { useEffect, useState } from "react";
import type { ProjectHealthEntry } from "@/types";
import { fetchProjectHealth } from "@/lib/api";

const ALERT_STYLES: Record<string, { label: string; color: string }> = {
  neglected: { label: "NEGLECTED", color: "bg-accent-yellow/15 text-accent-yellow" },
  deploy_broken: { label: "DEPLOY BROKEN", color: "bg-accent-red/15 text-accent-red" },
  circuit_breaker_tripped: { label: "CB TRIPPED", color: "bg-accent-red/15 text-accent-red" },
  pr_backlog: { label: "PR BACKLOG", color: "bg-accent-purple/15 text-accent-purple" },
};

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  return `${months[d.getMonth()]} ${d.getDate()}`;
}

function daysLabel(days: number | null): string {
  if (days === null) return "—";
  if (days < 1) return "<1d";
  return `${Math.round(days)}d`;
}

export default function ProjectHealth() {
  const [entries, setEntries] = useState<ProjectHealthEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchProjectHealth().then((r) => {
      if (r.data) setEntries(r.data);
      setLoading(false);
    });
  }, []);

  if (loading) {
    return (
      <div className="text-gray-600 text-sm py-8 text-center">
        Loading project health...
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <div className="text-gray-600 text-sm py-8 text-center">
        No projects found.
      </div>
    );
  }

  // Sort: projects with alerts first, then by days since last dispatch (descending)
  const sorted = [...entries].sort((a, b) => {
    if (a.alerts.length !== b.alerts.length) return b.alerts.length - a.alerts.length;
    const aDays = a.days_since_last_dispatch ?? 9999;
    const bDays = b.days_since_last_dispatch ?? 9999;
    return bDays - aDays;
  });

  return (
    <div className="space-y-4">
      <div className="text-[10px] uppercase tracking-wider text-gray-600 mono">
        Project Health Dashboard
      </div>

      <div className="bg-bg-surface rounded-lg border border-gray-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-[10px] uppercase tracking-wider text-gray-600">
              <th className="text-left px-4 py-2">Project</th>
              <th className="text-left px-4 py-2">Last Deploy</th>
              <th className="text-right px-4 py-2">Deploy Failures</th>
              <th className="text-right px-4 py-2">Last Activity</th>
              <th className="text-right px-4 py-2">Open PRs</th>
              <th className="text-right px-4 py-2">Sessions</th>
              <th className="text-left px-4 py-2">Alerts</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((entry) => (
              <tr
                key={entry.project}
                className={`border-b border-gray-800/30 hover:bg-bg-surface-alt/20 transition-colors ${
                  entry.alerts.length > 0 ? "bg-accent-red/5" : ""
                }`}
              >
                <td className="px-4 py-2 mono text-gray-200">
                  {entry.project}
                  {entry.paused && (
                    <span className="ml-1.5 text-[10px] bg-gray-600/15 text-gray-400 px-1.5 py-0.5 rounded">
                      PAUSED
                    </span>
                  )}
                </td>
                <td className="px-4 py-2 text-gray-400">
                  {formatDate(entry.last_successful_deploy)}
                  {entry.days_since_last_deploy !== null && (
                    <span className="text-gray-600 ml-1">
                      ({daysLabel(entry.days_since_last_deploy)} ago)
                    </span>
                  )}
                </td>
                <td className="px-4 py-2 text-right">
                  <span
                    className={`mono ${
                      entry.consecutive_deploy_failures >= 2
                        ? "text-accent-red"
                        : entry.consecutive_deploy_failures > 0
                          ? "text-accent-yellow"
                          : "text-gray-500"
                    }`}
                  >
                    {entry.consecutive_deploy_failures}
                  </span>
                </td>
                <td className="px-4 py-2 text-right">
                  <span
                    className={`mono ${
                      entry.days_since_last_dispatch !== null &&
                      entry.days_since_last_dispatch > 7
                        ? "text-accent-yellow"
                        : "text-gray-400"
                    }`}
                  >
                    {daysLabel(entry.days_since_last_dispatch)}
                  </span>
                </td>
                <td className="px-4 py-2 text-right">
                  <span
                    className={`mono ${
                      entry.open_prs !== null && entry.open_prs > 5
                        ? "text-accent-purple"
                        : "text-gray-400"
                    }`}
                  >
                    {entry.open_prs ?? "—"}
                  </span>
                </td>
                <td className="px-4 py-2 text-right mono text-gray-500">
                  {entry.total_sessions}
                </td>
                <td className="px-4 py-2">
                  <div className="flex gap-1 flex-wrap">
                    {entry.alerts.map((alert) => {
                      const style = ALERT_STYLES[alert] ?? {
                        label: alert.toUpperCase(),
                        color: "bg-gray-600/15 text-gray-400",
                      };
                      return (
                        <span
                          key={alert}
                          className={`${style.color} text-[10px] mono px-1.5 py-0.5 rounded`}
                        >
                          {style.label}
                        </span>
                      );
                    })}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
