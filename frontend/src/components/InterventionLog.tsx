import { useEffect, useMemo, useRef, useState } from "react";

import {
  CONNECTOR_ICON,
  DECISION_CLASS,
  DECISION_ICON,
  decisionLabel,
  eventMatchesNode,
  eventScore,
  formatClock,
  truncateMiddle,
} from "../lib/format";
import { useStore } from "../store";
import type { Decision } from "../types";

const DECISIONS: Decision[] = ["ALLOW", "MASK", "HOLD", "BLOCK"];
const CONFIRM =
  "This restores the previous state. Changes made after this action may also be lost for database snapshots.";

export function InterventionLog() {
  const events = useStore((state) => state.events);
  const sessionId = useStore((state) => state.sessionId);
  const view = useStore((state) => state.view);
  const filters = useStore((state) => state.filters);
  const toggleDecision = useStore((state) => state.toggleDecision);
  const setFilters = useStore((state) => state.setFilters);
  const clearNodeFilter = useStore((state) => state.clearNodeFilter);
  const selectEvent = useStore((state) => state.selectEvent);
  const selectedEventId = useStore((state) => state.selectedEventId);
  const rollbackEvent = useStore((state) => state.rollbackEvent);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const atBottomRef = useRef(true);
  const [busy, setBusy] = useState<string | null>(null);

  const rows = useMemo(() => {
    const text = filters.text.trim().toLowerCase();
    return events
      .filter((event) => !sessionId || event.session_id === sessionId)
      .filter((event) => filters.decisions.length === 0 || filters.decisions.includes(event.decision))
      .filter((event) => !text || event.target.toLowerCase().includes(text))
      .filter((event) => !filters.nodeId || eventMatchesNode(event, filters.nodeId));
  }, [events, sessionId, filters]);

  useEffect(() => {
    const container = scrollRef.current;
    if (!container || !atBottomRef.current) return;
    container.scrollTop = container.scrollHeight;
  }, [rows.length]);

  const undo = async (id: string) => {
    if (!window.confirm(CONFIRM)) return;
    setBusy(id);
    await rollbackEvent(id);
    setBusy(null);
  };

  function approvalLabel(approval: string | null | undefined): string {
    if (!approval) return "—";
    if (approval === "approved_masked") return "approved + masked";
    if (approval === "approved") return "approved";
    if (approval === "denied") return "denied";
    return approval;
  }

  function approvalColor(approval: string | null | undefined): string {
    if (approval === "approved_masked" || approval === "approved") return "text-green-700 font-semibold";
    if (approval === "denied") return "text-red-600 font-semibold";
    return "text-slate-500";
  }

  return (
    <section className="flex min-h-0 flex-col rounded-2xl border border-slate-300 bg-white shadow-sm">
      <header className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-4 py-2.5 bg-slate-50/70 rounded-t-2xl">
        <h2 className="text-sm font-bold text-[#2a0845]">Intervention log</h2>
        <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by decision">
          {DECISIONS.map((decision) => {
            const active = filters.decisions.includes(decision);
            return (
              <button
                key={decision}
                type="button"
                aria-pressed={active}
                onClick={() => toggleDecision(decision)}
                className={`rounded-full border px-2 py-0.5 text-[11px] transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 ${
                  active
                    ? DECISION_CLASS[decision]
                    : "border-slate-300 bg-white text-slate-600 hover:text-slate-900 shadow-xs"
                }`}
              >
                {decisionLabel(decision)}
              </button>
            );
          })}
        </div>
        <label className="sr-only" htmlFor="log-filter">
          Filter by target
        </label>
        <input
          id="log-filter"
          value={filters.text}
          onChange={(event) => setFilters({ text: event.target.value })}
          placeholder="Filter target…"
          className="w-40 rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs text-slate-800 placeholder:text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
        />
        {filters.nodeId ? (
          <button
            type="button"
            onClick={clearNodeFilter}
            className="rounded-full border border-[#2a0845]/40 bg-[#2a0845]/10 px-2 py-0.5 text-[11px] text-[#2a0845] hover:bg-[#2a0845]/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
          >
            node: {filters.nodeId} ✕
          </button>
        ) : null}
        <span className="ml-auto text-[11px] text-slate-500">{rows.length} rows</span>
      </header>

      <div
        ref={scrollRef}
        onScroll={(event) => {
          const element = event.currentTarget;
          atBottomRef.current =
            element.scrollHeight - element.scrollTop - element.clientHeight < 40;
        }}
        className="max-h-72 min-h-0 overflow-auto"
      >
        {rows.length === 0 ? (
          <p className="px-3 py-6 text-sm text-slate-500">
            No activity yet. Connect an agent to the gateway.
          </p>
        ) : (
          <table className="w-full border-collapse text-left text-xs">
            <thead className="sticky top-0 bg-[#2a0845] text-[10px] uppercase tracking-wide text-slate-200">
              <tr>
                <th className="px-2 py-1.5 font-medium">Time</th>
                <th className="px-2 py-1.5 font-medium">Connector</th>
                <th className="px-2 py-1.5 font-medium">Target</th>
                {view === "detail" ? <th className="px-2 py-1.5 font-medium">Tier</th> : null}
                {view === "detail" ? <th className="px-2 py-1.5 font-medium">Score</th> : null}
                <th className="px-2 py-1.5 font-medium">Decision</th>
                <th className="px-2 py-1.5 font-medium">Approval</th>
                <th className="px-2 py-1.5 font-medium">
                  {view === "simple" ? "Reason" : "Reason / hash"}
                </th>
                <th className="px-2 py-1.5 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((event) => (
                <tr
                  key={event.id}
                  tabIndex={0}
                  onClick={() => selectEvent(event.id)}
                  onKeyDown={(keyboard) => {
                    if (keyboard.key === "Enter" || keyboard.key === " ") {
                      keyboard.preventDefault();
                      selectEvent(event.id);
                    }
                  }}
                  className={`cursor-pointer border-t border-slate-200 align-top transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 ${
                    selectedEventId === event.id ? "bg-amber-50" : "hover:bg-slate-50"
                  }`}
                >
                  <td className="whitespace-nowrap px-2 py-1.5 tabular-nums text-slate-500">
                    {formatClock(event.ts)}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-[#2a0845] font-medium">
                    <span aria-hidden>{CONNECTOR_ICON[event.connector]}</span> {event.connector}
                  </td>
                  <td className="max-w-[12rem] px-2 py-1.5 font-mono text-[11px] text-slate-700">
                    <span title={event.target}>{truncateMiddle(event.target, 44)}</span>
                  </td>
                  {view === "detail" ? (
                    <td className="px-2 py-1.5 tabular-nums text-slate-600">{event.risk_tier}</td>
                  ) : null}
                  {view === "detail" ? (
                    <td className="px-2 py-1.5 tabular-nums text-slate-600">
                      {eventScore(event)}
                    </td>
                  ) : null}
                  <td className="whitespace-nowrap px-2 py-1.5">
                    <span
                      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${DECISION_CLASS[event.decision]}`}
                    >
                      <span aria-hidden>{DECISION_ICON[event.decision]}</span>
                      {decisionLabel(event.decision)}
                    </span>
                  </td>
                  <td className={`whitespace-nowrap px-2 py-1.5 text-[11px] ${approvalColor(event.approval)}`}>
                    {approvalLabel(event.approval)}
                  </td>
                  <td className="max-w-[20rem] px-2 py-1.5 text-slate-700">
                    <span className="block truncate" title={event.reason_text}>
                      {event.reason_text}
                    </span>
                    {view === "detail" && event.hash ? (
                      <span className="block font-mono text-[10px] text-slate-400">
                        {event.hash.slice(0, 8)}
                      </span>
                    ) : null}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5">
                    {event.undoable ? (
                      <button
                        type="button"
                        disabled={busy === event.id}
                        onClick={(click) => {
                          click.stopPropagation();
                          void undo(event.id);
                        }}
                        className="rounded border border-amber-600 bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-800 hover:bg-amber-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 disabled:opacity-50 shadow-sm"
                      >
                        {busy === event.id ? "Undoing..." : "Undo"}
                      </button>
                    ) : (
                      <span className="text-[10px] text-slate-400">-</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
