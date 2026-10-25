import { useEffect, useState } from "react";

import { useStore } from "../store";
import type { Approval, Resolution } from "../types";
import {
  CONNECTOR_ICON,
  CONNECTOR_LABEL,
  formatSecondsRemaining,
  truncateEnd,
} from "../lib/format";

export function ApprovalCard({ approval }: { approval: Approval }) {
  const view = useStore((state) => state.view);
  const resolveApproval = useStore((state) => state.resolveApproval);
  const [remaining, setRemaining] = useState(() => formatSecondsRemaining(approval.expires_at));
  const [pending, setPending] = useState<Resolution | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    setRemaining(formatSecondsRemaining(approval.expires_at));
    const timer = window.setInterval(
      () => setRemaining(formatSecondsRemaining(approval.expires_at)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [approval.expires_at]);

  const act = async (resolution: Resolution) => {
    if (pending) return;
    setPending(resolution);
    await resolveApproval(approval.id, resolution);
    setPending(null);
  };

  const summary = approval.summary ?? "";
  const long = summary.length > 200;
  const options = new Set(approval.options ?? []);

  return (
    <article className="animate-[pulse_1.2s_ease-in-out_2] rounded-2xl border-2 border-amber-400 bg-amber-50/90 p-3.5 shadow-md shadow-amber-500/10">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-bold text-[#2a0845]">{approval.reason_text}</p>
        <span
          className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold tabular-nums ${
            remaining <= 10
              ? "border-red-500/50 bg-red-500/10 text-red-300"
              : "border-amber-300 bg-white text-amber-950 font-bold"
          }`}
          aria-label={`${remaining} seconds remaining`}
        >
          {remaining}s
        </span>
      </div>

      <div className="mt-2 flex items-center gap-2 text-xs text-slate-600 font-medium">
        <span aria-hidden>{CONNECTOR_ICON[approval.connector]}</span>
        <span>{CONNECTOR_LABEL[approval.connector]}</span>
        <span className="text-slate-400">·</span>
        <span className="truncate">{approval.target}</span>
        {view === "detail" ? (
          <>
            <span className="text-slate-400">·</span>
            <span>tier {approval.tier}</span>
          </>
        ) : null}
      </div>

      <pre
        className={`mt-2 overflow-x-auto rounded-lg border border-amber-200/90 bg-white p-2.5 font-mono text-xs text-slate-900 shadow-inner ${
          expanded ? "" : "max-h-24 overflow-y-hidden"
        }`}
      >
        {expanded ? summary : truncateEnd(summary, 200)}
      </pre>
      {long ? (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="mt-1 text-[11px] text-sky-300 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
        >
          {expanded ? "Show less" : "Show full request"}
        </button>
      ) : null}

      {view === "detail" ? (
        <dl className="mt-2 grid grid-cols-2 gap-1 text-[11px] font-semibold text-slate-500">
          <div className="flex justify-between gap-2">
            <dt>pattern</dt>
            <dd className="tabular-nums text-slate-800 font-bold">{approval.scores.pattern.toFixed(2)}</dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt>location</dt>
            <dd className="tabular-nums text-slate-800 font-bold">{approval.scores.location.toFixed(2)}</dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt>content</dt>
            <dd className="tabular-nums text-slate-800 font-bold">{approval.scores.content.toFixed(2)}</dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt>behaviour</dt>
            <dd className="tabular-nums text-slate-800 font-bold">{approval.scores.behaviour.toFixed(2)}</dd>
          </div>
        </dl>
      ) : null}

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={pending !== null}
          onClick={() => void act("approve")}
          className="rounded-lg bg-emerald-600 px-3.5 py-1.5 text-xs font-bold text-white transition hover:bg-emerald-500 shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300 disabled:opacity-50"
        >
          {pending === "approve" ? "Approving…" : "Approve"}
        </button>
        {options.has("approve_masked") ? (
          <button
            type="button"
            disabled={pending !== null}
            onClick={() => void act("approve_masked")}
            className="rounded-lg bg-[#f59e0b] px-3.5 py-1.5 text-xs font-bold text-slate-950 transition hover:bg-[#d97706] shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 disabled:opacity-50"
          >
            {pending === "approve_masked" ? "Approving…" : "Approve with masking"}
          </button>
        ) : null}
        <button
          type="button"
          disabled={pending !== null}
          onClick={() => void act("deny_and_lock")}
          className="rounded-lg border border-rose-300 bg-rose-50 px-3.5 py-1.5 text-xs font-bold text-rose-700 transition hover:bg-rose-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300 disabled:opacity-50"
        >
          {pending === "deny_and_lock" ? "Stopping…" : "Stop"}
        </button>
        <button
          type="button"
          disabled={pending !== null}
          onClick={() => void act("deny")}
          className="ml-auto self-center text-[11px] font-semibold text-slate-500 underline-offset-2 hover:text-slate-800 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 disabled:opacity-50"
        >
          Deny only
        </button>
      </div>
    </article>
  );
}
