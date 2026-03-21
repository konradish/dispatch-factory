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
