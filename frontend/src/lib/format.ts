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
  ALLOW: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  MASK: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  HOLD: "bg-sky-500/15 text-sky-300 border-sky-500/40",
  BLOCK: "bg-red-500/15 text-red-300 border-red-500/40",
};

export const NODE_STATE_META: Record<
  NodeState,
  { label: string; icon: string; color: string; css: string }
> = {
  unvisited: { label: "Unvisited", icon: "○", color: "#71717a", css: "text-zinc-400" },
  accessed: { label: "Accessed", icon: "●", color: "#34d399", css: "text-emerald-300" },
  masked: { label: "Masked", icon: "◐", color: "#fbbf24", css: "text-amber-300" },
  held: { label: "Held", icon: "⏸", color: "#60a5fa", css: "text-sky-300" },
  blocked: { label: "Blocked", icon: "🔒", color: "#f87171", css: "text-red-300" },
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
    mark: "bg-red-500/30 text-red-100 underline decoration-red-400 decoration-2",
    dot: "bg-red-400",
  },
  personal: {
    label: "Personal data",
    mark: "bg-amber-500/30 text-amber-50 underline decoration-amber-400 decoration-2",
    dot: "bg-amber-400",
  },
  financial: {
    label: "Financial",
    mark: "bg-purple-500/30 text-purple-50 underline decoration-purple-400 decoration-2",
    dot: "bg-purple-400",
  },
  logic: {
    label: "Code logic",
    mark: "bg-sky-500/30 text-sky-50 underline decoration-sky-400 decoration-2",
    dot: "bg-sky-400",
  },
  other: {
    label: "Other",
    mark: "bg-zinc-500/30 text-zinc-100 underline decoration-zinc-400 decoration-2",
    dot: "bg-zinc-400",
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
