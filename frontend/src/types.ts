// Matches /api/sessions response
export interface SessionSummary {
  id: string;
  project: string;
  type: "worker" | "deploy" | "validate";
  state: string; // running, planning, reviewing, verifying, monitoring, completed, deployed, rolled_back, error, abandoned
  task: string;
  artifact_types: string[];
  has_log: boolean;
}

// Matches /api/sessions/:id response
export interface SessionDetail {
  id: string;
  state: string;
  artifacts: Record<string, unknown>;
  has_log: boolean;
  log_size: number;
}

// Matches /api/sessions/active response
export interface ActiveSession {
  id: string;
  active: boolean;
}

export interface TicketRequest {
  task: string;
  project: string;
  flags: string[];
}

export interface TicketResponse {
  status: "dispatched" | "error";
  stdout: string;
  stderr: string;
}

export interface TerminalInfo {
  session_name: string;
  port: number;
}

// Matches /api/sessions/history response
export interface HistorySession {
  id: string;
  project: string;
  type: "worker" | "deploy" | "validate";
  task: string;
  state: string;
  mtime: number;
  artifact_types: string[];
  has_log: boolean;
  summary: {
    verdict: string;
    feedback: string;
    deploy_status: string;
    stages: Record<string, string>;
    healed: boolean;
    healer_action: string;
    healer_diagnosis: string;
  };
}

// Matches /api/brief response
export interface Brief {
  direction: string;
  stats: {
    total_sessions: number;
    deployed: number;
    completed: number;
    failed: number;
    healed: number;
    success_rate: number;
  };
  projects: Record<
    string,
    { deployed: number; completed: number; failed: number; total: number }
  >;
}

// Matches /api/backlog response
export interface BacklogTicket {
  id: string;
  task: string;
  project: string;
  priority: "low" | "normal" | "high" | "urgent";
  flags: string;
  status: "intake" | "needs_input" | "ready" | "pending" | "dispatched" | "completed" | "failed" | "cancelled";
  source: string;
  session_id: string | null;
  created_at: string;
  dispatched_at: string | null;
  completed_at: string | null;
}

// Matches /api/heartbeat response
export interface HeartbeatState {
  last_beat: string;
  beats: number;
  last_actions: string[];
  auto_dispatch_enabled: boolean;
  max_concurrent: number;
  started_at: string;
  uptime_seconds: number;
}

// Matches /api/self-improvement response
export interface SelfImprovementState {
  product_dispatches_since_last_self_improvement: number;
  total_product_dispatches: number;
  total_self_improvement_dispatches: number;
  self_improvement_due: boolean;
  last_self_improvement_at: number | null;
  last_updated: number;
}

// Matches /api/pipeline/summary response
export interface PipelineStageSummary {
  id: string;
  name: string;
  phase: number | string;
  description: string;
  enabled: boolean | string;
  engine: string;
  model: string;
  timeout: number | null;
  healer: { enabled: boolean; actions: string[] } | null;
  outputs: string[];
}

export interface PipelineSummary {
  version: string;
  global: {
    session_timeout_minutes: number;
    deploy_window: [number, number];
    stage_timeout_seconds: number;
  };
  stages: PipelineStageSummary[];
  dispatch_bin: string;
}

// Matches /api/pipeline/stages/:id response
export interface PipelineStageDetail extends PipelineStageSummary {
  [key: string]: unknown;
}

// Matches /api/log response
export interface LogEvent {
  timestamp: number;
  session: string;
  project: string;
  type:
    | "dispatched"
    | "planned"
    | "reviewed"
    | "verified"
    | "healed"
    | "monitored"
    | "completed"
    | "abandoned"
    | "error";
  description: string;
}

// Matches /api/healer-effectiveness response
export interface HealerEffectiveness {
  total_healed: number;
  deployed: number;
  completed_unverified: number;
  failed: number;
  true_success_rate: number;
  false_confidence_rate: number;
  sessions: {
    session: string;
    project: string;
    state: string;
    deploy_status: string;
    healer_action: string;
    category: "deployed" | "completed_unverified" | "failed" | "other";
  }[];
}

// Matches /api/cleared-healed-sessions response
export interface ClearedHealedSession {
  cleared_at: number;
  reason: string;
  project?: string;
  source: string;
}

// Matches /api/project-health response
export interface ProjectHealthEntry {
  project: string;
  last_successful_deploy: string | null;
  days_since_last_deploy: number | null;
  consecutive_deploy_failures: number;
  circuit_breaker_tripped: boolean;
  days_since_last_dispatch: number | null;
  last_dispatch_date: string | null;
  open_prs: number | null;
  total_sessions: number;
  paused: boolean;
  alerts: string[];
}
