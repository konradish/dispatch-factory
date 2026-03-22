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

export function fetchPipelineSummary(): Promise<ApiResult<PipelineSummary>> {
  return request<PipelineSummary>("/api/pipeline/summary");
}

export function fetchPipelineStage(
  id: string
): Promise<ApiResult<PipelineStageDetail>> {
  return request<PipelineStageDetail>(`/api/pipeline/stages/${id}`);
}
