import { create } from "zustand";

import { api, openSocket, setApproverToken, type SocketHandle } from "./api";
import type {
  Approval,
  ChainVerification,
  ConnectionState,
  Decision,
  EventRow,
  GraphPayload,
  MetricsSummary,
  PolicyView,
  Resolution,
  SessionDetail,
  SessionSummary,
  WsMessage,
} from "./types";

export type ViewMode = "simple" | "detail";

export interface Filters {
  decisions: Decision[];
  text: string;
  nodeId: string | null;
}

interface AppState {
  token: string | null;
  authError: string | null;
  connection: ConnectionState;
  loading: boolean;
  error: string | null;
  sessions: SessionSummary[];
  sessionId: string | null;
  session: SessionDetail | null;
  events: EventRow[];
  approvals: Approval[];
  graph: GraphPayload | null;
  policy: PolicyView | null;
  metrics: MetricsSummary | null;
  verify: ChainVerification | null;
  selectedEventId: string | null;
  view: ViewMode;
  filters: Filters;

  setToken: (token: string | null) => void;
  setAuthError: (message: string | null) => void;
  setError: (message: string | null) => void;
  setView: (view: ViewMode) => void;
  selectEvent: (id: string | null) => void;
  selectSession: (id: string) => Promise<void>;
  setFilters: (filters: Partial<Filters>) => void;
  toggleDecision: (decision: Decision) => void;
  clearNodeFilter: () => void;

  hydrate: (token: string) => Promise<void>;
  refreshAll: () => Promise<void>;
  refreshEvents: (afterSeq?: number) => Promise<void>;
  refreshApprovals: () => Promise<void>;
  refreshGraph: () => Promise<void>;
  refreshMetrics: () => Promise<void>;
  verifyLog: () => Promise<void>;
  resolveApproval: (id: string, resolution: Resolution) => Promise<void>;
  lockSession: () => Promise<void>;
  unlockSession: () => Promise<void>;
  rollbackEvent: (id: string) => Promise<void>;
  applyMessage: (message: WsMessage) => void;
  disconnect: () => void;
}

let socket: SocketHandle | null = null;

function mergeEvents(existing: EventRow[], incoming: EventRow[]): EventRow[] {
  const byId = new Map<string, EventRow>();
  for (const event of [...existing, ...incoming]) byId.set(event.id, event);
  return [...byId.values()].sort((left, right) => {
    const leftSeq = left.seq ?? 0;
    const rightSeq = right.seq ?? 0;
    if (leftSeq !== rightSeq) return leftSeq - rightSeq;
    return left.ts.localeCompare(right.ts);
  });
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export const useStore = create<AppState>((set, get) => ({
  token: null,
  authError: null,
  connection: "idle",
  loading: false,
  error: null,
  sessions: [],
  sessionId: null,
  session: null,
  events: [],
  approvals: [],
  graph: null,
  policy: null,
  metrics: null,
  verify: null,
  selectedEventId: null,
  view: "simple",
  filters: { decisions: [], text: "", nodeId: null },

  setToken: (token) => {
    setApproverToken(token);
    set({ token, authError: token ? null : get().authError });
  },
  setAuthError: (message) => set({ authError: message, connection: "unauthorized" }),
  setError: (message) => set({ error: message }),
  setView: (view) => set({ view }),
  selectEvent: (id) => set({ selectedEventId: id }),
  setFilters: (filters) =>
    set((state) => ({ filters: { ...state.filters, ...filters } })),
  toggleDecision: (decision) =>
    set((state) => {
      const active = state.filters.decisions;
      const decisions = active.includes(decision)
        ? active.filter((item) => item !== decision)
        : [...active, decision];
      return { filters: { ...state.filters, decisions } };
    }),
  clearNodeFilter: () =>
    set((state) => ({ filters: { ...state.filters, nodeId: null } })),

  async hydrate(token) {
    setApproverToken(token);
    set({ token, loading: true, error: null });
    try {
      const [policy, sessions, approvals] = await Promise.all([
        api.policy(),
        api.sessions(),
        api.approvals("pending"),
      ]);
      const sessionId = get().sessionId ?? sessions[0]?.id ?? null;
      set({ policy, sessions, approvals, sessionId });
      if (sessionId) {
        const [session, events, graph, metrics] = await Promise.all([
          api.session(sessionId),
          api.events({ session_id: sessionId, limit: 1000 }),
          api.graph(sessionId),
          api.metrics(sessionId),
        ]);
        set({ session, events, graph, metrics });
      }
      socket?.close();
      socket = openSocket(token, {
        onMessage: (message) => get().applyMessage(message),
        onStatus: (connection) => set({ connection }),
        onAuthError: () => {
          setApproverToken(null);
          set({ token: null, authError: "Invalid or expired token" });
        },
        onResync: () => {
          const lastSeq = get().events.at(-1)?.seq ?? undefined;
          void get().refreshEvents(lastSeq);
          void get().refreshApprovals();
          void get().refreshGraph();
        },
      });
    } catch (error) {
      set({ error: messageOf(error) });
    } finally {
      set({ loading: false });
    }
  },

  async refreshAll() {
    const { sessionId } = get();
    if (!sessionId) return;
    try {
      const [sessions, session, events, graph, approvals, metrics] = await Promise.all([
        api.sessions(),
        api.session(sessionId),
        api.events({ session_id: sessionId, limit: 1000 }),
        api.graph(sessionId),
        api.approvals("pending"),
        api.metrics(sessionId),
      ]);
      set({ sessions, session, events, graph, approvals, metrics, error: null });
    } catch (error) {
      set({ error: messageOf(error) });
    }
  },

  async refreshEvents(afterSeq) {
    const { sessionId } = get();
    if (!sessionId) return;
    try {
      const events = await api.events({
        session_id: sessionId,
        limit: 1000,
        after_seq: afterSeq,
      });
      set((state) => ({
        events: mergeEvents(afterSeq ? state.events : [], events),
        error: null,
      }));
    } catch (error) {
      set({ error: messageOf(error) });
    }
  },

  async refreshApprovals() {
    try {
      const approvals = await api.approvals("pending");
      set({ approvals });
    } catch (error) {
      set({ error: messageOf(error) });
    }
  },

  async refreshGraph() {
    const { sessionId } = get();
    if (!sessionId) return;
    try {
      const graph = await api.graph(sessionId);
      set({ graph });
    } catch (error) {
      set({ error: messageOf(error) });
    }
  },

  async refreshMetrics() {
    const { sessionId } = get();
    if (!sessionId) return;
    try {
      const metrics = await api.metrics(sessionId);
      set({ metrics });
    } catch {
      /* metrics are informative only */
    }
  },

  async selectSession(id) {
    set({ sessionId: id, selectedEventId: null, events: [], graph: null, metrics: null });
    try {
      const [session, events, graph, metrics] = await Promise.all([
        api.session(id),
        api.events({ session_id: id, limit: 1000 }),
        api.graph(id),
        api.metrics(id),
      ]);
      set({ session, events, graph, metrics, error: null });
    } catch (error) {
      set({ error: messageOf(error) });
    }
  },

  async verifyLog() {
    try {
      const verify = await api.verifyLog();
      set({ verify, error: null });
    } catch (error) {
      set({ error: messageOf(error), verify: null });
    }
  },

  async resolveApproval(id, resolution) {
    try {
      await api.resolve(id, resolution);
      set((state) => ({
        approvals: state.approvals.filter((approval) => approval.id !== id),
      }));
      await get().refreshEvents();
      await get().refreshMetrics();
    } catch (error) {
      set({ error: messageOf(error) });
    }
  },

  async lockSession() {
    const { sessionId } = get();
    if (!sessionId) return;
    try {
      const session = await api.lockSession(sessionId);
      set({ session });
    } catch (error) {
      set({ error: messageOf(error) });
    }
  },

  async unlockSession() {
    const { sessionId } = get();
    if (!sessionId) return;
    try {
      const session = await api.unlockSession(sessionId);
      set({ session });
    } catch (error) {
      set({ error: messageOf(error) });
    }
  },

  async rollbackEvent(id) {
    try {
      await api.rollback(id);
      await get().refreshEvents();
      await get().refreshMetrics();
      await get().refreshGraph();
    } catch (error) {
      set({ error: messageOf(error) });
    }
  },

  applyMessage(message) {
    const state = get();
    switch (message.type) {
      case "hello":
        set({
          approvals: message.pending_approvals.filter(
            (approval) => !state.sessionId || approval.session_id === state.sessionId,
          ),
        });
        return;
      case "event.created": {
        const event = message.event;
        if (state.sessionId && event.session_id !== state.sessionId) return;
        set({ events: mergeEvents(state.events, [event]) });
        void get().refreshMetrics();
        return;
      }
      case "approval.requested": {
        const approval = message.approval;
        if (state.sessionId && approval.session_id !== state.sessionId) return;
        set({ approvals: [approval, ...state.approvals.filter((item) => item.id !== approval.id)] });
        return;
      }
      case "approval.resolved":
        set({ approvals: state.approvals.filter((item) => item.id !== message.approval_id) });
        return;
      case "session.updated":
        if (message.session.id === state.sessionId) set({ session: message.session });
        set({
          sessions: state.sessions.map((session) =>
            session.id === message.session.id ? { ...session, ...message.session } : session,
          ),
        });
        return;
      case "graph.node": {
        if (!state.graph) return;
        const nodes = state.graph.nodes.map((node) =>
          node.id === message.node_id
            ? { ...node, state: message.state, last_ts: message.last_ts ?? node.last_ts }
            : node,
        );
        set({ graph: { ...state.graph, nodes } });
        return;
      }
      case "snapshot.rolled_back":
        void get().refreshEvents();
        return;
      default:
        return;
    }
  },

  disconnect() {
    socket?.close();
    socket = null;
  },
}));
