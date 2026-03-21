export interface Session {
  id: string;
  project: string;
  task: string;
  station: string;
  status:
    | "processing"
    | "healing"
    | "queued"
    | "held"
    | "completed"
    | "failed";
  elapsed_seconds: number;
  started_at: string;
  artifacts: Record<string, unknown>;
  held: boolean;
}

export interface TicketRequest {
  task: string;
  project: string;
  flags: string[];
}

export interface TicketResponse {
  session_id: string;
  status: "dispatched" | "error";
  message: string;
}

export interface TerminalInfo {
  session_name: string;
  port: number;
  read_only: boolean;
}
