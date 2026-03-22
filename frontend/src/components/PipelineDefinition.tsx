import { useState, useEffect } from "react";
import type { PipelineSummary, PipelineStageSummary, PipelineStageDetail } from "@/types";
import { fetchPipelineSummary, fetchPipelineStage } from "@/lib/api";

const ENGINE_COLORS: Record<string, string> = {
  claude_reason: "border-accent-purple",
  "cy -p": "border-accent-cyan",
  subprocess: "border-accent-blue",
  "playwright + claude_reason": "border-accent-purple",
  template: "border-gray-600",
};

const ENGINE_BG: Record<string, string> = {
  claude_reason: "bg-accent-purple/5",
  "cy -p": "bg-accent-cyan/5",
  subprocess: "bg-accent-blue/5",
  "playwright + claude_reason": "bg-accent-purple/5",
  template: "bg-gray-800/30",
};

function engineBorderClass(engine: string): string {
  if (engine.includes("playwright") && engine.includes("claude_reason")) {
    return "border-accent-purple border-r-accent-cyan";
  }
  return ENGINE_COLORS[engine] || "border-gray-600";
}

function engineBgClass(engine: string): string {
  if (engine.includes("playwright") && engine.includes("claude_reason")) {
    return "bg-accent-purple/5";
  }
  return ENGINE_BG[engine] || "bg-gray-800/30";
}

function enabledIndicator(enabled: boolean | string) {
  if (enabled === true) return <span className="inline-block w-2 h-2 rounded-full bg-accent-green" title="Enabled" />;
  if (enabled === "auto") return <span className="inline-block w-2 h-2 rounded-full bg-accent-yellow" title="Auto" />;
  return <span className="inline-block w-2 h-2 rounded-full bg-accent-red" title="Disabled" />;
}

function abbreviateModel(model: string): string {
  if (!model) return "";
  return model.replace("claude-", "").replace("opus-4-6", "opus-4-6");
}

function formatDeployWindow(window: [number, number]): string {
  const fmt = (h: number) => `${String(h).padStart(2, "0")}:00`;
  return `${fmt(window[0])} - ${fmt(window[1])}`;
}

function ArrowConnector({ hasHealer }: { hasHealer: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center flex-shrink-0" style={{ width: 40 }}>
      <div className="relative flex items-center w-full h-6">
        <div className="flex-1 h-px bg-gray-600" />
        <svg width="8" height="12" viewBox="0 0 8 12" className="flex-shrink-0 text-gray-600">
          <path d="M1 1 L7 6 L1 11" stroke="currentColor" fill="none" strokeWidth="1.5" />
        </svg>
      </div>
      {hasHealer && (
        <svg width="32" height="16" viewBox="0 0 32 16" className="text-accent-yellow mt-0.5" aria-label="Retry loop">
          <path d="M28 2 C28 12, 4 12, 4 2" stroke="currentColor" fill="none" strokeWidth="1.2" strokeDasharray="3 2" />
          <path d="M1 4 L4 1 L7 4" stroke="currentColor" fill="none" strokeWidth="1.2" />
        </svg>
      )}
    </div>
  );
}

function StageCard({
  stage,
  isSelected,
  onClick,
}: {
  stage: PipelineStageSummary;
  isSelected: boolean;
  onClick: () => void;
}) {
  const borderClass = engineBorderClass(stage.engine);
  const bgClass = engineBgClass(stage.engine);

  return (
    <button
      onClick={onClick}
      className={`
        relative flex-shrink-0 rounded-lg border-2 p-3 text-left transition-all cursor-pointer
        hover:brightness-125 hover:scale-[1.02]
        ${borderClass} ${bgClass}
        ${isSelected ? "ring-1 ring-white/30 brightness-125" : ""}
      `}
      style={{ width: 152, minHeight: 120 }}
    >
      {/* Phase badge */}
      <span className="absolute top-1.5 left-2 text-[10px] mono text-gray-500">
        P{stage.phase}
      </span>

      {/* Enabled indicator */}
      <span className="absolute top-2 right-2">
        {enabledIndicator(stage.enabled)}
      </span>

      {/* Name */}
      <div className="mt-3 text-sm font-semibold text-gray-200 leading-tight">
        {stage.name}
      </div>

      {/* Engine */}
      <div className="mt-1.5 mono text-[10px] text-gray-400 truncate">
        {stage.engine}
      </div>

      {/* Model */}
      {stage.model && (
        <div className="mono text-[10px] text-gray-500 truncate">
          {abbreviateModel(stage.model)}
        </div>
      )}

      {/* Timeout */}
      {stage.timeout !== null && (
        <div className="mt-1 text-[10px] text-gray-500">
          {stage.timeout}s timeout
        </div>
      )}

      {/* Healer badge */}
      {stage.healer?.enabled && (
        <span className="absolute bottom-1.5 right-2 text-[9px] mono font-semibold px-1.5 py-0.5 rounded bg-accent-yellow/20 text-accent-yellow">
          HEALER
        </span>
      )}
    </button>
  );
}

function DetailPanel({
  stage,
  stageDetail,
  loading,
}: {
  stage: PipelineStageSummary;
  stageDetail: PipelineStageDetail | null;
  loading: boolean;
}) {
  const [jsonExpanded, setJsonExpanded] = useState(false);

  return (
    <div className="mt-4 rounded-lg border border-gray-700 bg-bg-surface p-5 animate-slide-in">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-base font-semibold text-gray-200">{stage.name}</h3>
          <span className="mono text-xs text-gray-500">Phase {stage.phase} &middot; {stage.engine}</span>
        </div>
        <span className="text-xs text-gray-500">
          {stage.enabled === true ? "Enabled" : stage.enabled === "auto" ? "Auto-enabled" : "Disabled"}
        </span>
      </div>

      <p className="mt-3 text-sm text-gray-400 leading-relaxed">{stage.description}</p>

      {/* Outputs */}
      <div className="mt-4">
        <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1.5">Outputs</h4>
        <div className="flex flex-wrap gap-1.5">
          {stage.outputs.map((o) => (
            <span key={o} className="mono text-xs px-2 py-0.5 rounded bg-bg-surface-alt text-gray-300">
              {o}
            </span>
          ))}
        </div>
      </div>

      {/* Engine & Model */}
      <div className="mt-4 grid grid-cols-2 gap-4">
        <div>
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">Engine</h4>
          <span className="mono text-sm text-gray-300">{stage.engine}</span>
        </div>
        {stage.model && (
          <div>
            <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">Model</h4>
            <span className="mono text-sm text-gray-300">{stage.model}</span>
          </div>
        )}
      </div>

      {/* Healer */}
      {stage.healer?.enabled && (
        <div className="mt-4">
          <h4 className="text-xs font-semibold text-accent-yellow uppercase tracking-wider mb-1.5">Healer Actions</h4>
          <div className="flex flex-wrap gap-1.5">
            {stage.healer.actions.map((a) => (
              <span key={a} className="mono text-xs px-2 py-0.5 rounded bg-accent-yellow/10 text-accent-yellow">
                {a}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Full JSON */}
      <div className="mt-4">
        <button
          onClick={() => setJsonExpanded(!jsonExpanded)}
          className="text-xs text-gray-500 hover:text-gray-300 transition-colors cursor-pointer"
        >
          {jsonExpanded ? "Hide" : "Show"} full config JSON
        </button>
        {jsonExpanded && (
          <pre className="mt-2 p-3 rounded bg-bg-base text-xs mono text-gray-400 overflow-x-auto max-h-64 overflow-y-auto">
            {loading
              ? "Loading..."
              : JSON.stringify(stageDetail || stage, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}

export default function PipelineDefinition() {
  const [summary, setSummary] = useState<PipelineSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedStageId, setSelectedStageId] = useState<string | null>(null);
  const [stageDetail, setStageDetail] = useState<PipelineStageDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    fetchPipelineSummary().then((res) => {
      if (res.data) {
        setSummary(res.data);
      } else {
        setError(res.error || "Failed to load pipeline definition");
      }
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    if (!selectedStageId) {
      setStageDetail(null);
      return;
    }
    setDetailLoading(true);
    fetchPipelineStage(selectedStageId).then((res) => {
      if (res.data) setStageDetail(res.data);
      setDetailLoading(false);
    });
  }, [selectedStageId]);

  function handleStageClick(stageId: string) {
    setSelectedStageId((prev) => (prev === stageId ? null : stageId));
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <span className="mono text-sm text-gray-500">Loading pipeline definition...</span>
      </div>
    );
  }

  if (error || !summary) {
    return (
      <div className="flex items-center justify-center py-20">
        <span className="mono text-sm text-accent-red">{error || "No data"}</span>
      </div>
    );
  }

  const selectedStage = summary.stages.find((s) => s.id === selectedStageId) || null;

  return (
    <div>
      {/* Global config bar */}
      <div className="flex flex-wrap items-center gap-6 px-4 py-2.5 rounded-lg bg-bg-surface border border-gray-800 mb-6">
        <span className="mono text-xs text-gray-400">
          <span className="text-gray-600">version</span> {summary.version}
        </span>
        <span className="mono text-xs text-gray-400">
          <span className="text-gray-600">session timeout</span> {summary.global.session_timeout_minutes}m
        </span>
        <span className="mono text-xs text-gray-400">
          <span className="text-gray-600">deploy window</span> {formatDeployWindow(summary.global.deploy_window)}
        </span>
        <span className="mono text-xs text-gray-400">
          <span className="text-gray-600">stage timeout</span> {summary.global.stage_timeout_seconds}s
        </span>
        <span className="ml-auto mono text-[10px] text-gray-600 truncate max-w-xs" title={summary.dispatch_bin}>
          {summary.dispatch_bin}
        </span>
      </div>

      {/* Engine legend */}
      <div className="flex flex-wrap items-center gap-4 mb-5 px-1">
        <span className="text-[10px] text-gray-600 uppercase tracking-wider">Engine</span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-0.5 bg-accent-purple inline-block rounded" />
          <span className="text-[10px] text-gray-500">LLM reasoning</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-0.5 bg-accent-cyan inline-block rounded" />
          <span className="text-[10px] text-gray-500">Claude Code</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-0.5 bg-accent-blue inline-block rounded" />
          <span className="text-[10px] text-gray-500">subprocess</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-0.5 bg-gray-600 inline-block rounded" />
          <span className="text-[10px] text-gray-500">template</span>
        </span>
      </div>

      {/* Flow diagram */}
      <div className="overflow-x-auto pb-4">
        <div className="flex items-center gap-0 min-w-max px-1">
          {summary.stages.map((stage, i) => (
            <div key={stage.id} className="flex items-center">
              {i > 0 && (
                <ArrowConnector
                  hasHealer={summary.stages[i - 1].healer?.enabled === true}
                />
              )}
              <StageCard
                stage={stage}
                isSelected={selectedStageId === stage.id}
                onClick={() => handleStageClick(stage.id)}
              />
            </div>
          ))}
        </div>
      </div>

      {/* Detail panel */}
      {selectedStage && (
        <DetailPanel
          stage={selectedStage}
          stageDetail={stageDetail}
          loading={detailLoading}
        />
      )}

      {/* Footer note */}
      <p className="mt-8 text-[11px] text-gray-600 text-center">
        Pipeline definition is currently read-only. Future: LLM operator can modify stages at runtime.
      </p>
    </div>
  );
}
