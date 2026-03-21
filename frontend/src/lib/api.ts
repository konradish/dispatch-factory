import type {
  Session,
  TicketRequest,
  TicketResponse,
  TerminalInfo,
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

export function fetchSessions(): Promise<ApiResult<Session[]>> {
  return request<Session[]>("/api/sessions");
}

export function fetchSession(id: string): Promise<ApiResult<Session>> {
  return request<Session>(`/api/sessions/${id}`);
}

export function fetchActiveSessions(): Promise<ApiResult<Session[]>> {
  return request<Session[]>("/api/sessions/active");
}

export function createTicket(
  ticket: TicketRequest
): Promise<ApiResult<TicketResponse>> {
  return request<TicketResponse>("/api/tickets", {
    method: "POST",
    body: JSON.stringify(ticket),
  });
}

export function holdSession(id: string): Promise<ApiResult<{ ok: boolean }>> {
  return request<{ ok: boolean }>(`/api/sessions/${id}/hold`, {
    method: "POST",
  });
}

export function killSession(id: string): Promise<ApiResult<{ ok: boolean }>> {
  return request<{ ok: boolean }>(`/api/sessions/${id}/kill`, {
    method: "POST",
  });
}

export function attachTerminal(
  name: string
): Promise<ApiResult<TerminalInfo>> {
  return request<TerminalInfo>(`/api/terminal/${name}/attach`, {
    method: "POST",
  });
}

export function detachTerminal(
  name: string
): Promise<ApiResult<{ ok: boolean }>> {
  return request<{ ok: boolean }>(`/api/terminal/${name}/detach`, {
    method: "POST",
  });
}

export function fetchTerminals(): Promise<ApiResult<TerminalInfo[]>> {
  return request<TerminalInfo[]>("/api/terminal");
}
