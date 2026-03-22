// Matches /api/sessions response
export interface SessionSummary {
  id: string;
  project: string;
  type: "worker" | "deploy" | "validate";
  state: string; // running, planning, reviewing, verifying, monitoring, completed, deployed, rolled_back, error
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
    | "error";
  description: string;
}
