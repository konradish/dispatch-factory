import { useEffect, useState } from "react";
import type { LogEvent } from "@/types";
import { fetchFactoryLog } from "@/lib/api";

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

const TYPE_STYLES: Record<string, { label: string; color: string }> = {
  dispatched: { label: "DISPATCHED", color: "bg-accent-blue text-accent-blue" },
  planned: { label: "PLANNED", color: "bg-accent-purple text-accent-purple" },
  reviewed: { label: "REVIEWED", color: "bg-accent-cyan text-accent-cyan" },
  verified: { label: "VERIFIED", color: "bg-accent-green text-accent-green" },
  healed: { label: "HEALED", color: "bg-accent-yellow text-accent-yellow" },
  monitored: { label: "MONITORED", color: "bg-accent-purple text-accent-purple" },
  completed: { label: "COMPLETED", color: "bg-gray-500 text-gray-400" },
  error: { label: "ERROR", color: "bg-accent-red text-accent-red" },
};

function formatTimestamp(ts: number): string {
  const d = new Date(ts * 1000);
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  const month = months[d.getMonth()];
  const day = d.getDate();
  const hours = String(d.getHours()).padStart(2, "0");
  const mins = String(d.getMinutes()).padStart(2, "0");
  return `${month} ${day} ${hours}:${mins}`;
}

function dayKey(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleDateString("en-US", {
    weekday: "long",
    month: "short",
    day: "numeric",
  });
}

function groupByDay(events: LogEvent[]): Map<string, LogEvent[]> {
  const groups = new Map<string, LogEvent[]>();
  for (const event of events) {
    const key = dayKey(event.timestamp);
    const arr = groups.get(key);
    if (arr) {
      arr.push(event);
    } else {
      groups.set(key, [event]);
    }
  }
  return groups;
}

export default function FactoryLog() {
  const [events, setEvents] = useState<LogEvent[]>([]);

  useEffect(() => {
    fetchFactoryLog().then((r) => {
      if (r.data) setEvents(r.data);
    });
  }, []);

  if (events.length === 0) {
    return (
      <div className="text-gray-600 text-sm py-8 text-center">
        No log entries yet.
      </div>
    );
  }

  const grouped = groupByDay(events);

  return (
    <div className="space-y-6">
      {Array.from(grouped.entries()).map(([day, dayEvents]) => (
        <div key={day}>
          <div className="text-[10px] uppercase tracking-wider text-gray-600 mb-2 mono">
            {day}
          </div>
          <div className="bg-bg-surface rounded-lg border border-gray-800 overflow-hidden">
            {dayEvents.map((event, i) => {
              const style = TYPE_STYLES[event.type] ?? {
                label: event.type.toUpperCase(),
                color: "bg-gray-600 text-gray-400",
              };
              const [bgClass, textClass] = style.color.split(" ");
              return (
                <div
                  key={`${event.session}-${event.timestamp}-${i}`}
                  className={`flex items-start gap-3 px-4 py-2 ${
                    i < dayEvents.length - 1
                      ? "border-b border-gray-800/30"
                      : ""
                  } hover:bg-bg-surface-alt/20 transition-colors`}
                >
                  <span className="mono text-[11px] text-gray-600 whitespace-nowrap shrink-0 pt-0.5">
                    {formatTimestamp(event.timestamp)}
                  </span>
                  <span
                    className={`${bgClass}/15 ${textClass} text-[10px] mono px-1.5 py-0.5 rounded shrink-0`}
                  >
                    {style.label}
                  </span>
                  {event.project && (
                    <span className="inline-flex items-center gap-1 shrink-0">
                      <span
                        className="h-1.5 w-1.5 rounded-full inline-block"
                        style={{
                          backgroundColor: projectColor(event.project),
                        }}
                      />
                      <span className="text-[11px] text-gray-500">
                        {event.project}
                      </span>
                    </span>
                  )}
                  <span className="text-xs text-gray-400 leading-relaxed">
                    {event.description}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
