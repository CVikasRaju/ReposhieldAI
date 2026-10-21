/** Types for every dashboard API payload (docs/04 part B). */

export type Decision = "ALLOW" | "MASK" | "HOLD" | "BLOCK";
export type ConnectorName = "filesystem" | "command" | "database" | "network" | "system";
export type FinalOutcome =
  | "executed"
  | "executed_masked"
  | "blocked"
  | "denied"
  | "timeout"
  | "terminated"
  | "error";
export type ApprovalKind = "auto" | "approved" | "approved_masked" | "denied" | "timeout";
export type Resolution = "approve" | "approve_masked" | "deny" | "deny_and_lock";
export type ApprovalStatus =
  | "pending"
  | "approved"
  | "approved_masked"
  | "denied"
  | "timeout"
  | "cancelled";
export type SessionStatus = "active" | "locked" | "ended";
export type NodeState = "unvisited" | "accessed" | "masked" | "held" | "blocked";
export type NodeType = "dir" | "file" | "database" | "table" | "host";

export interface SignalScores {
  pattern: number;
  location: number;
  content: number;
  behaviour: number;
  combined: number;
}

export interface Detection {
  kind?: string;
  start?: number;
  end?: number;
  placeholder?: string;
  confidence?: number;
  reason_code?: string;
  [key: string]: unknown;
}

export interface EventRow {
  seq: number | null;
  id: string;
  session_id: string;
  ts: string;
  connector: ConnectorName;
  action: string;
  target: string;
  task_context: string | null;
  risk_tier: number;
  scores: SignalScores;
  decision: Decision;
  final_outcome: FinalOutcome;
  approval: ApprovalKind;
  reason_codes: string[];
  reason_text: string;
  detections: Detection[];
  flags: string[];
  content_sha256: string | null;
  delta_sha256: string | null;
  latency_ms: number | null;
  prev_hash: string | null;
  hash: string | null;
  undoable: boolean;
}

export interface SessionSummary {
  id: string;
  name: string | null;
  profile: string;
  task: string | null;
  status: SessionStatus;
  locked_reason: string | null;
  agent_pid: number | null;
  started_at: string;
  ended_at: string | null;
}

export interface SessionDetail extends SessionSummary {
  counters?: Record<Decision, number>;
  total_events?: number;
  latency_mean_ms?: number;
}

export interface Approval {
  id: string;
  session_id: string;
  connector: ConnectorName;
  action: string;
  target: string;
  summary: string;
  tier: number;
  reason_codes: string[];
  reason_text: string;
  scores: SignalScores;
  options: Resolution[];
  status: ApprovalStatus;
  resolved_by: string | null;
  created_at: string;
  expires_at: string;
  resolved_at: string | null;
  event_id: string | null;
}

export interface Change {
  raw_start: number;
  raw_end: number;
  san_start: number;
  san_end: number;
  kind: string;
  reason_code: string;
  placeholder: string;
}

export interface Delta {
  event_id: string;
  raw: string | null;
  sanitized: string | null;
  changes: Change[];
}

export interface GraphNode {
  id: string;
  label?: string;
  type: NodeType;
  parent: string | null;
  state: NodeState;
  last_ts?: string;
}

export interface GraphEdge {
  source: string;
  target: string;
}

export interface GraphPayload {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface PolicyView {
  profile: string;
  mode: string;
  task: { description: string; scope_paths: string[] };
  thresholds: { mask: number; hold: number; block: number };
  protect: Array<Record<string, unknown>>;
  approval: { timeout_seconds: number; channels: string[] };
  limits: Record<string, unknown>;
  policy_path?: string;
  file_exists?: boolean;
}

export interface PolicySample {
  filename: string;
  yaml: string;
  exists: boolean;
  policy_path: string;
}

export interface CreatePolicyResult {
  ok: boolean;
  created: boolean;
  message: string;
  path: string;
}

export interface ChainVerification {
  ok: boolean;
  checked: number;
  first_bad_seq: number | null;
  reason: string | null;
}

export interface MetricsSummary {
  session_id: string | null;
  counts: Record<Decision, number>;
  total: number;
  latency_mean_ms: number;
  latency_p95_ms: number;
}

export interface RollbackResult {
  ok: boolean;
  event_id: string;
  rolled_back_event_id: string;
  snapshot_id: string;
}

export interface ResolveResult {
  ok: boolean;
  approval: Approval | null;
}

export type WsMessage =
  | { type: "hello"; pending_approvals: Approval[] }
  | { type: "event.created"; event: EventRow }
  | { type: "approval.requested"; approval: Approval }
  | { type: "approval.resolved"; approval_id: string; resolution: string }
  | { type: "session.updated"; session: SessionDetail }
  | { type: "graph.node"; node_id: string; state: NodeState; last_ts?: string }
  | { type: "snapshot.rolled_back"; event_id: string };

export type ConnectionState =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "unauthorized";
