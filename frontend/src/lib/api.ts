import type {
  SessionSummary,
  SessionDetail,
  ActiveSession,
  TicketRequest,
  TicketResponse,
  HistorySession,
  Brief,
  LogEvent,
  BacklogTicket,
  HeartbeatState,
  SelfImprovementState,
  MetaWorkRatio,
  ProjectHealthEntry,
  PipelineSummary,
  PipelineStageDetail,
} from "@/types";

interface ApiResult<T> {
  data: T | null;
  error: string | null;
}

async function request<T>(
  url: string,
  options?: RequestInit
): Promise<ApiResult<T>> {
  try {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!res.ok) {
      const body = await res.text();
      return { data: null, error: `${res.status}: ${body}` };
    }
    const data = (await res.json()) as T;
    return { data, error: null };
  } catch (err) {
    return {
      data: null,
      error: err instanceof Error ? err.message : "Unknown error",
    };
  }
}

export function fetchSessions(limit = 20): Promise<ApiResult<SessionSummary[]>> {
  return request<SessionSummary[]>(`/api/sessions?limit=${limit}`);
}

export function fetchSession(id: string): Promise<ApiResult<SessionDetail>> {
  return request<SessionDetail>(`/api/sessions/${id}`);
}

export function fetchActiveSessions(): Promise<ApiResult<ActiveSession[]>> {
  return request<ActiveSession[]>("/api/sessions/active");
}

export function createTicket(
  ticket: TicketRequest
): Promise<ApiResult<TicketResponse>> {
  return request<TicketResponse>("/api/tickets", {
    method: "POST",
    body: JSON.stringify(ticket),
  });
}

export function holdSession(
  id: string
): Promise<ApiResult<{ status: string; output: string }>> {
  return request<{ status: string; output: string }>(
    `/api/sessions/${id}/hold`,
    { method: "POST" }
  );
}

export function killSession(
  id: string
): Promise<ApiResult<{ status: string; output: string }>> {
  return request<{ status: string; output: string }>(
    `/api/sessions/${id}/kill`,
    { method: "POST" }
  );
}

export function attachTerminal(
  name: string
): Promise<ApiResult<{ port: number; session: string }>> {
  return request<{ port: number; session: string }>(
    `/api/terminal/${name}/attach`,
    { method: "POST" }
  );
}

export function detachTerminal(
  name: string
): Promise<ApiResult<{ status: string }>> {
  return request<{ status: string }>(`/api/terminal/${name}/detach`, {
    method: "POST",
  });
}

export function fetchTerminals(): Promise<ApiResult<Record<string, number>>> {
  return request<Record<string, number>>("/api/terminal");
}

export function fetchHistory(
  limit = 50
): Promise<ApiResult<HistorySession[]>> {
  return request<HistorySession[]>(`/api/sessions/history?limit=${limit}`);
}

export function fetchBrief(): Promise<ApiResult<Brief>> {
  return request<Brief>("/api/brief");
}

export function fetchFactoryLog(
  limit = 100
): Promise<ApiResult<LogEvent[]>> {
  return request<LogEvent[]>(`/api/log?limit=${limit}`);
}

export function fetchBacklog(
  status?: string
): Promise<ApiResult<BacklogTicket[]>> {
  const qs = status ? `?status=${status}` : "";
  return request<BacklogTicket[]>(`/api/backlog${qs}`);
}

export function createBacklogTicket(ticket: {
  task: string;
  project: string;
  priority: string;
  flags: string[];
  status?: string;
}): Promise<ApiResult<BacklogTicket>> {
  return request<BacklogTicket>("/api/backlog", {
    method: "POST",
    body: JSON.stringify(ticket),
  });
}

export function deleteBacklogTicket(
  id: string
): Promise<ApiResult<{ status: string }>> {
  return request<{ status: string }>(`/api/backlog/${id}`, {
    method: "DELETE",
  });
}

export function updateBacklogTicket(
  id: string,
  updates: Record<string, unknown>
): Promise<ApiResult<BacklogTicket>> {
  return request<BacklogTicket>(`/api/backlog/${id}`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
}

export function dispatchBacklogTicket(
  id: string
): Promise<ApiResult<{ status: string }>> {
  return request<{ status: string }>(`/api/backlog/${id}/dispatch`, {
    method: "POST",
  });
}

export function fetchHeartbeat(): Promise<ApiResult<HeartbeatState>> {
  return request<HeartbeatState>("/api/heartbeat");
}

export function toggleAutoDispatch(
  enabled: boolean,
  maxConcurrent: number
): Promise<ApiResult<{ status: string }>> {
  return request<{ status: string }>(
    `/api/heartbeat/auto-dispatch?enabled=${enabled}&max_concurrent=${maxConcurrent}`,
    { method: "POST" }
  );
}

export function fetchSelfImprovement(): Promise<ApiResult<SelfImprovementState>> {
  return request<SelfImprovementState>("/api/self-improvement");
}

export function fetchMetaWorkRatio(): Promise<ApiResult<MetaWorkRatio>> {
  return request<MetaWorkRatio>("/api/meta-work-ratio");
}

export function fetchPipelineSummary(): Promise<ApiResult<PipelineSummary>> {
  return request<PipelineSummary>("/api/pipeline/summary");
}

export function fetchPipelineStage(
  id: string
): Promise<ApiResult<PipelineStageDetail>> {
  return request<PipelineStageDetail>(`/api/pipeline/stages/${id}`);
}

export function fetchProjectHealth(): Promise<
  ApiResult<ProjectHealthEntry[]>
> {
  return request<ProjectHealthEntry[]>("/api/project-health");
}

export interface ForemanResult {
  lens: string;
  assessment: string;
  observations: string;
  actions: { type: string; status: string; detail?: string; reason?: string; [key: string]: unknown }[];
  raw_actions: unknown[];
  timestamp: number;
}

export function foremanChat(message: string, threadId = "default"): Promise<ApiResult<ForemanResult>> {
  return request<ForemanResult>("/api/foreman/chat", {
    method: "POST",
    body: JSON.stringify({ message, thread_id: threadId }),
  });
}

// Session live output
export interface SessionOutput {
  session_id: string;
  lines: string[];
  alive: boolean;
}

export function fetchSessionOutput(id: string, lines = 20): Promise<ApiResult<SessionOutput>> {
  return request<SessionOutput>(`/api/sessions/${id}/output?lines=${lines}`);
}

// Foreman chat threads & history
export interface ChatThread {
  id: string;
  title: string;
  created_at: number;
  last_message_at: number;
  message_count: number;
  summary: string | null;
}

export interface ChatMessage {
  role: "human" | "foreman";
  text: string;
  actions: ForemanResult["actions"];
  timestamp: number;
}

export function fetchThreads(): Promise<ApiResult<ChatThread[]>> {
  return request<ChatThread[]>("/api/foreman/threads");
}

export function createThread(title?: string): Promise<ApiResult<{ id: string; title: string }>> {
  return request<{ id: string; title: string }>("/api/foreman/threads", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export function fetchChatHistory(threadId = "default", limit = 50): Promise<ApiResult<ChatMessage[]>> {
  return request<ChatMessage[]>(`/api/foreman/chat/history?thread_id=${threadId}&limit=${limit}`);
}
