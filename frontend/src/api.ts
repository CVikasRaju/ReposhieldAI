import type {
  Approval,
  ChainVerification,
  ConnectionState,
  Delta,
  EventRow,
  GraphPayload,
  MetricsSummary,
  PolicySample,
  PolicyView,
  CreatePolicyResult,
  Resolution,
  ResolveResult,
  RollbackResult,
  SessionDetail,
  SessionSummary,
  WsMessage,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";
const WS_URL = `${API_BASE.replace(/^http/, "ws")}/ws`;

let approverToken: string | null = null;

export function setApproverToken(token: string | null): void {
  approverToken = token;
}

export function getApproverToken(): string | null {
  return approverToken;
}

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (approverToken) headers.set("X-Approver-Token", approverToken);
  if (init.body !== undefined) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (response.status === 401) {
    throw new ApiError(401, "unauthorized", "Invalid or expired token");
  }
  if (!response.ok) {
    let code = "request_failed";
    let message = `Request failed with status ${response.status}`;
    try {
      const body = (await response.json()) as { error?: string; message?: string };
      if (body.error) code = body.error;
      if (body.message) message = body.message;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, code, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  health: () => request<{ status: string; version: string }>("/api/health"),
  sessions: () => request<SessionSummary[]>("/api/sessions"),
  session: (id: string) => request<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}`),
  lockSession: (id: string) =>
    request<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}/lock`, { method: "POST" }),
  unlockSession: (id: string) =>
    request<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}/unlock`, { method: "POST" }),
  events: (params: { session_id?: string; limit?: number; after_seq?: number } = {}) => {
    const search = new URLSearchParams();
    if (params.session_id) search.set("session_id", params.session_id);
    if (params.limit) search.set("limit", String(params.limit));
    if (params.after_seq !== undefined) search.set("after_seq", String(params.after_seq));
    const query = search.toString();
    return request<EventRow[]>(`/api/events${query ? `?${query}` : ""}`);
  },
  event: (id: string) => request<EventRow>(`/api/events/${encodeURIComponent(id)}`),
  delta: (id: string) => request<Delta>(`/api/events/${encodeURIComponent(id)}/delta`),
  rollback: (id: string) =>
    request<RollbackResult>(`/api/events/${encodeURIComponent(id)}/rollback`, { method: "POST" }),
  graph: (sessionId?: string) =>
    request<GraphPayload>(`/api/graph${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`),
  approvals: (status?: string) =>
    request<Approval[]>(`/api/approvals${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  resolve: (id: string, resolution: Resolution) =>
    request<ResolveResult>(`/api/approvals/${encodeURIComponent(id)}/resolve`, {
      method: "POST",
      body: JSON.stringify({ resolution }),
    }),
  verifyLog: () => request<ChainVerification>("/api/log/verify"),
  policy: () => request<PolicyView>("/api/policy"),
  policySample: () => request<PolicySample>("/api/policy/sample"),
  createSamplePolicy: (overwrite = false) =>
    request<CreatePolicyResult>("/api/policy/create-sample", {
      method: "POST",
      body: JSON.stringify({ overwrite }),
    }),
  metrics: (sessionId?: string) =>
    request<MetricsSummary>(
      `/api/metrics/summary${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`,
    ),
};

export interface SocketHandlers {
  onMessage: (message: WsMessage) => void;
  onStatus: (state: ConnectionState) => void;
  onAuthError: () => void;
  onResync: () => void;
}

export interface SocketHandle {
  close: () => void;
}

/** Open the dashboard socket: auth first message, exponential backoff 1-15 s. */
export function openSocket(token: string, handlers: SocketHandlers): SocketHandle {
  let closed = false;
  let socket: WebSocket | null = null;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let hadHello = false;

  const schedule = (): void => {
    if (closed) return;
    attempt += 1;
    const delay = Math.min(15_000, 1000 * 2 ** (attempt - 1));
    handlers.onStatus("reconnecting");
    timer = setTimeout(connect, delay);
  };

  function connect(): void {
    if (closed) return;
    handlers.onStatus(attempt === 0 ? "connecting" : "reconnecting");
    const ws = new WebSocket(WS_URL);
    socket = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "auth", token }));
    };
    ws.onmessage = (rawEvent) => {
      let message: WsMessage;
      try {
        message = JSON.parse(String(rawEvent.data)) as WsMessage;
      } catch {
        return;
      }
      if (message.type === "hello") {
        const reconnected = hadHello;
        hadHello = true;
        attempt = 0;
        handlers.onStatus("open");
        handlers.onMessage(message);
        if (reconnected) handlers.onResync();
        return;
      }
      handlers.onMessage(message);
    };
    ws.onclose = (event) => {
      if (closed) return;
      if (event.code === 4401) {
        closed = true;
        handlers.onStatus("unauthorized");
        handlers.onAuthError();
        return;
      }
      schedule();
    };
  }

  connect();

  return {
    close: () => {
      closed = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    },
  };
}
