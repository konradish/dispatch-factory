// Matches /api/sessions response
export interface SessionSummary {
  id: string;
  project: string;
  type: "worker" | "deploy" | "validate";
  state: string; // running, planning, reviewing, verifying, monitoring, completed, deployed, rolled_back, error
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
