import { useStore } from "../store";
import {
  CONNECTOR_ICON,
  DECISION_CLASS,
  DECISION_ICON,
  decisionLabel,
  formatClock,
} from "../lib/format";

export function ActivityFeed() {
  const events = useStore((state) => state.events);
  const sessionId = useStore((state) => state.sessionId);
  const selectedEventId = useStore((state) => state.selectedEventId);
  const selectEvent = useStore((state) => state.selectEvent);

  const visible = [...events]
    .filter((event) => !sessionId || event.session_id === sessionId)
    .reverse();

  return (
    <section className="flex min-h-0 flex-col rounded-2xl border border-slate-300 bg-white shadow-sm">
      <header className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5 bg-slate-50/70 rounded-t-2xl">
        <h2 className="text-sm font-bold text-[#2a0845]">Activity</h2>
        <span className="text-[11px] font-medium text-slate-500">{visible.length} events</span>
      </header>
      <ol className="min-h-0 flex-1 overflow-y-auto p-2">
        {visible.length === 0 ? (
          <li className="px-2 py-6 text-sm text-slate-500">
            No activity yet. Connect an agent to the gateway.
          </li>
        ) : null}
        {visible.map((event) => (
          <li key={event.id}>
            <button
              type="button"
              onClick={() => selectEvent(event.id)}
              aria-pressed={selectedEventId === event.id}
              className={`mb-1 flex w-full animate-[slide-in_180ms_ease-out] items-start gap-2 rounded-lg border px-2 py-1.5 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                selectedEventId === event.id
                  ? "border-amber-400 bg-amber-50/70 shadow-xs"
                  : "border-transparent hover:border-slate-200 hover:bg-slate-50"
              }`}
            >
              <span
                aria-hidden
                className={`mt-0.5 shrink-0 rounded border px-1 text-[10px] ${DECISION_CLASS[event.decision]}`}
                title={decisionLabel(event.decision)}
              >
                {DECISION_ICON[event.decision]}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-semibold text-slate-900">{event.reason_text}</span>
                <span className="mt-0.5 flex items-center gap-1.5 text-[11px] font-medium text-slate-500">
                  <span className="tabular-nums">{formatClock(event.ts)}</span>
                  <span>·</span>
                  <span aria-hidden>{CONNECTOR_ICON[event.connector]}</span>
                  <span>{event.connector}</span>
                  <span>·</span>
                  <span>{decisionLabel(event.decision)}</span>
                </span>
              </span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}
