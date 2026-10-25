import type {
  ConnectorName,
  Decision,
  EventRow,
  GraphNode,
  NodeState,
  Resolution,
} from "../types";

export const DECISION_LABEL: Record<Decision, string> = {
  ALLOW: "Allowed",
  MASK: "Hidden parts",
  HOLD: "Paused",
  BLOCK: "Blocked",
};

export const DECISION_ICON: Record<Decision, string> = {
  ALLOW: "●",
  MASK: "◐",
  HOLD: "⏸",
  BLOCK: "⛔",
};

export const DECISION_CLASS: Record<Decision, string> = {
  ALLOW: "bg-emerald-50 text-emerald-800 border-emerald-300 font-medium",
  MASK: "bg-amber-50 text-amber-900 border-amber-300 font-medium",
  HOLD: "bg-sky-50 text-sky-900 border-sky-300 font-medium",
  BLOCK: "bg-rose-50 text-rose-800 border-rose-300 font-medium",
};

export const NODE_STATE_META: Record<
  NodeState,
  { label: string; icon: string; color: string; css: string }
> = {
  unvisited: { label: "Unvisited", icon: "○", color: "#94a3b8", css: "text-slate-500" },
  accessed: { label: "Accessed", icon: "●", color: "#10b981", css: "text-emerald-700" },
  masked: { label: "Masked", icon: "◐", color: "#f59e0b", css: "text-amber-800" },
  held: { label: "Held", icon: "⏸", color: "#3b82f6", css: "text-sky-700" },
  blocked: { label: "Blocked", icon: "🔒", color: "#ef4444", css: "text-rose-700" },
};

export const CONNECTOR_ICON: Record<ConnectorName, string> = {
  filesystem: "📄",
  command: "⌘",
  database: "🗄",
  network: "🌐",
  system: "⚙",
};

export const CONNECTOR_LABEL: Record<ConnectorName, string> = {
  filesystem: "Files",
  command: "Shell",
  database: "Database",
  network: "Network",
  system: "System",
};

export const RESOLUTION_LABEL: Record<Resolution, string> = {
  approve: "Approve",
  approve_masked: "Approve with masking",
  deny: "Deny only",
  deny_and_lock: "Stop",
};

export function decisionLabel(decision: Decision): string {
  return DECISION_LABEL[decision] ?? decision;
}

export function formatClock(iso: string): string {
  const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatSecondsRemaining(expiresAt: string): number {
  const end = new Date(expiresAt.endsWith("Z") || expiresAt.includes("+") ? expiresAt : `${expiresAt}Z`);
  return Math.max(0, Math.ceil((end.getTime() - Date.now()) / 1000));
}

export function truncateMiddle(value: string, max = 48): string {
  if (value.length <= max) return value;
  const keep = Math.max(6, Math.floor((max - 3) / 2));
  return `${value.slice(0, keep)}…${value.slice(-keep)}`;
}

export function truncateEnd(value: string, max = 200): string {
  return value.length <= max ? value : `${value.slice(0, max)}…`;
}

export type ChangeCategory = "secret" | "personal" | "financial" | "logic" | "other";

const PERSONAL_KINDS = ["NAME", "EMAIL", "PHONE", "NATIONAL", "PII", "ADDRESS", "DOB", "SSN", "CONTACT"];
const FINANCIAL_KINDS = ["CARD", "IBAN", "ACCOUNT", "SALARY", "INVOICE", "BANK"];
const LOGIC_KINDS = ["LOGIC", "CODE", "FUNCTION", "BLOCK"];

export function changeCategory(kind: string): ChangeCategory {
  const upper = (kind || "").toUpperCase();
  if (LOGIC_KINDS.some((token) => upper.includes(token))) return "logic";
  if (PERSONAL_KINDS.some((token) => upper.includes(token))) return "personal";
  if (FINANCIAL_KINDS.some((token) => upper.includes(token))) return "financial";
  if (!upper) return "other";
  return "secret";
}

export const CATEGORY_META: Record<
  ChangeCategory,
  { label: string; mark: string; dot: string }
> = {
  secret: {
    label: "Secret",
    mark: "bg-red-100 text-red-900 underline decoration-red-500 decoration-2 font-medium",
    dot: "bg-red-500",
  },
  personal: {
    label: "Personal data",
    mark: "bg-amber-100 text-amber-900 underline decoration-amber-500 decoration-2 font-medium",
    dot: "bg-amber-500",
  },
  financial: {
    label: "Financial",
    mark: "bg-purple-100 text-purple-900 underline decoration-purple-500 decoration-2 font-medium",
    dot: "bg-purple-500",
  },
  logic: {
    label: "Code logic",
    mark: "bg-sky-100 text-sky-900 underline decoration-sky-500 decoration-2 font-medium",
    dot: "bg-sky-500",
  },
  other: {
    label: "Other",
    mark: "bg-slate-100 text-slate-800 underline decoration-slate-400 decoration-2 font-medium",
    dot: "bg-slate-400",
  },
};

export function nodeLabel(node: GraphNode): string {
  const meta = NODE_STATE_META[node.state] ?? NODE_STATE_META.unvisited;
  const name = node.label ?? node.id;
  return `${meta.icon} ${name}`;
}

/** Does an event belong to a graph node (path, table or host)? */
export function eventMatchesNode(event: EventRow, nodeId: string): boolean {
  const target = event.target ?? "";
  if (target === nodeId) return true;
  const stripped = nodeId.replace(/^(db|host):/, "");
  if (target === stripped) return true;
  if (target.startsWith(`${stripped}/`) || nodeId.startsWith(`${target}/`)) return true;
  return false;
}

export function approvalLabel(approval: string): string {
  switch (approval) {
    case "auto":
      return "auto";
    case "approved":
      return "approved";
    case "approved_masked":
      return "approved with masking";
    case "denied":
      return "denied";
    case "timeout":
      return "timeout";
    default:
      return approval;
  }
}

/** Optional detail fields shown only in the detail view. */
export function eventScore(event: EventRow): string {
  return Number(event.scores?.combined ?? 0).toFixed(2);
}
