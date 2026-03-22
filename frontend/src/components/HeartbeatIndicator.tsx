import { useState, useEffect, useRef, useCallback } from "react";
import type { HeartbeatState } from "@/types";
import { fetchHeartbeat, toggleAutoDispatch } from "@/lib/api";

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h${m}m`;
}

export default function HeartbeatIndicator() {
  const [hb, setHb] = useState<HeartbeatState | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [maxConcurrent, setMaxConcurrent] = useState(3);
  const ref = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const result = await fetchHeartbeat();
    if (result.error) {
      setError(result.error);
      setHb(null);
    } else if (result.data) {
      setHb(result.data);
      setMaxConcurrent(result.data.max_concurrent);
      setError(null);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, [load]);

  // Close popover on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", handleClick);
      return () => document.removeEventListener("mousedown", handleClick);
    }
  }, [open]);

  async function handleToggleAuto() {
    if (!hb) return;
    const result = await toggleAutoDispatch(!hb.auto_dispatch_enabled, maxConcurrent);
    if (result.error) {
      setError(result.error);
    } else {
      load();
    }
  }

  async function handleMaxConcurrentChange(val: number) {
    setMaxConcurrent(val);
    if (!hb) return;
    await toggleAutoDispatch(hb.auto_dispatch_enabled, val);
    load();
  }

  if (error && !hb) {
    return (
      <div className="flex items-center gap-1.5">
        <span className="h-2 w-2 rounded-full bg-accent-red" />
        <span className="text-xs text-gray-500">offline</span>
      </div>
    );
  }

  if (!hb) {
    return (
      <div className="flex items-center gap-1.5">
        <span className="h-2 w-2 rounded-full bg-gray-600 animate-pulse" />
        <span className="text-xs text-gray-500">...</span>
      </div>
    );
  }

  const actionCount = hb.last_actions?.length ?? 0;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 hover:bg-bg-surface-alt/50 rounded px-2 py-1 transition-colors"
      >
        <span className="h-2 w-2 rounded-full bg-accent-green animate-pulse" />
        <span className="text-[10px] mono text-gray-500">
          beat #{hb.beats}
        </span>
        <span className="text-[10px] text-gray-600">
          {formatUptime(hb.uptime_seconds)}
        </span>
        {hb.auto_dispatch_enabled && (
          <span className="text-[9px] mono font-semibold px-1 py-0.5 rounded bg-accent-cyan/20 text-accent-cyan">
            AUTO
          </span>
        )}
        {actionCount > 0 && (
          <span className="text-[9px] mono font-semibold px-1 py-0.5 rounded bg-orange-500/20 text-orange-400">
            {actionCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute top-full right-0 mt-2 w-72 bg-bg-surface border border-gray-700 rounded-lg shadow-xl z-50 animate-slide-in">
          <div className="p-4 space-y-3">
            <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
              Heartbeat
            </div>

            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="text-gray-500">Last beat</div>
              <div className="mono text-gray-300 text-right">
                {new Date(hb.last_beat).toLocaleTimeString()}
              </div>
              <div className="text-gray-500">Beat count</div>
              <div className="mono text-gray-300 text-right">{hb.beats}</div>
              <div className="text-gray-500">Uptime</div>
              <div className="mono text-gray-300 text-right">
                {formatUptime(hb.uptime_seconds)}
              </div>
              <div className="text-gray-500">Started</div>
              <div className="mono text-gray-300 text-right">
                {new Date(hb.started_at).toLocaleString(undefined, {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </div>
            </div>

            <div className="border-t border-gray-800 pt-3">
              <label className="flex items-center justify-between cursor-pointer">
                <span className="text-xs text-gray-400">Auto-dispatch</span>
                <input
                  type="checkbox"
                  checked={hb.auto_dispatch_enabled}
                  onChange={handleToggleAuto}
                  className="h-4 w-4 rounded border-gray-600 bg-bg-base text-accent-cyan focus:ring-accent-cyan/50"
                />
              </label>
            </div>

            <div className="flex items-center justify-between">
              <span className="text-xs text-gray-400">Max concurrent</span>
              <input
                type="number"
                min={1}
                max={10}
                value={maxConcurrent}
                onChange={(e) =>
                  handleMaxConcurrentChange(parseInt(e.target.value, 10) || 1)
                }
                className="w-14 bg-bg-base border border-gray-700 rounded px-2 py-1 text-xs mono text-gray-200 text-right focus:outline-none focus:border-accent-cyan"
              />
            </div>

            {hb.last_actions && hb.last_actions.length > 0 && (
              <div className="border-t border-gray-800 pt-3">
                <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">
                  Recent Actions
                </div>
                <div className="space-y-1 max-h-32 overflow-y-auto">
                  {hb.last_actions.map((action, i) => (
                    <div
                      key={i}
                      className="text-xs mono text-gray-400 truncate"
                    >
                      {action}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
