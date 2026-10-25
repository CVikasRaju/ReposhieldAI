import { useEffect, useMemo, useState, type ReactNode } from "react";

import { api } from "../api";
import {
  CATEGORY_META,
  changeCategory,
  type ChangeCategory,
} from "../lib/format";
import { useStore } from "../store";
import type { Change, Delta } from "../types";

const CAP = 200_000;

interface Range {
  start: number;
  end: number;
  kind: string;
  reason: string;
}

function highlighted(text: string, ranges: Range[]): ReactNode[] {
  const ordered = [...ranges]
    .filter((range) => range.end > range.start)
    .sort((left, right) => left.start - right.start);
  const parts: ReactNode[] = [];
  let cursor = 0;
  ordered.forEach((range, index) => {
    const start = Math.min(Math.max(range.start, cursor), text.length);
    const end = Math.min(Math.max(range.end, start), text.length);
    if (start > cursor) {
      parts.push(<span key={`plain-${index}`}>{text.slice(cursor, start)}</span>);
    }
    if (end > start) {
      const category = changeCategory(range.kind);
      parts.push(
        <mark
          key={`mark-${index}`}
          className={`rounded-sm px-0.5 ${CATEGORY_META[category].mark}`}
          title={`${CATEGORY_META[category].label}: ${range.kind} (${range.reason})`}
        >
          {text.slice(start, end)}
        </mark>,
      );
    }
    cursor = Math.max(cursor, end);
  });
  if (cursor < text.length) parts.push(<span key="tail">{text.slice(cursor)}</span>);
  return parts;
}

export function DeltaView() {
  const selectedEventId = useStore((state) => state.selectedEventId);
  const [delta, setDelta] = useState<Delta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [revealed, setRevealed] = useState(false);

  useEffect(() => {
    setRevealed(false);
    if (!selectedEventId) {
      setDelta(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .delta(selectedEventId)
      .then((result) => {
        if (!cancelled) setDelta(result);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedEventId]);

  const rawRanges = useMemo<Range[]>(
    () =>
      (delta?.changes ?? []).map((change: Change) => ({
        start: change.raw_start,
        end: change.raw_end,
        kind: change.kind,
        reason: change.reason_code,
      })),
    [delta],
  );

  const sanitizedRanges = useMemo<Range[]>(
    () =>
      (delta?.changes ?? []).map((change: Change) => ({
        start: change.san_start,
        end: change.san_end,
        kind: change.kind,
        reason: change.reason_code,
      })),
    [delta],
  );

  const counts = useMemo(() => {
    const result = new Map<ChangeCategory, number>();
    for (const change of delta?.changes ?? []) {
      const category = changeCategory(change.kind);
      result.set(category, (result.get(category) ?? 0) + 1);
    }
    return result;
  }, [delta]);

  const raw = delta?.raw ?? null;
  const sanitized = delta?.sanitized ?? null;
  const truncated = (raw?.length ?? 0) > CAP || (sanitized?.length ?? 0) > CAP;

  return (
    <section className="flex min-h-0 flex-col rounded-2xl border border-slate-300 bg-white shadow-sm">
      <header className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5 bg-slate-50/70 rounded-t-2xl">
        <h2 className="text-sm font-semibold text-zinc-200">Delta</h2>
        <span className="text-[11px] font-medium text-slate-500">
          {selectedEventId ? selectedEventId : "select an event"}
        </span>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {!selectedEventId ? (
          <p className="text-sm text-zinc-500">Select an event to see what changed.</p>
        ) : loading ? (
          <p className="text-sm text-zinc-500">Loading delta…</p>
        ) : error ? (
          <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-2 text-sm text-red-200">
            {error}
          </div>
        ) : !delta || (raw === null && sanitized === null) ? (
          <p className="text-sm text-zinc-500">Nothing was sent to the agent.</p>
        ) : (
          <div className="space-y-3">
            <div>
              <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-600">
                What the agent asked for
              </h3>
              <div className="relative">
                <pre
                  className={`max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-zinc-800 bg-zinc-950 p-2 font-mono text-[11px] leading-relaxed text-zinc-300 transition ${
                    revealed ? "" : "blur-sm select-none"
                  }`}
                >
                  {raw === null ? "(nothing)" : highlighted(raw.slice(0, CAP), rawRanges)}
                </pre>
                {!revealed ? (
                  <button
                    type="button"
                    onClick={() => setRevealed(true)}
                    className="absolute inset-0 flex items-center justify-center rounded-lg bg-zinc-950/40 text-xs font-medium text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  >
                    Click to reveal raw content
                  </button>
                ) : null}
              </div>
            </div>

            <div>
              <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-600">
                What the agent received
              </h3>
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-zinc-800 bg-zinc-950 p-2 font-mono text-[11px] leading-relaxed text-zinc-300">
                {sanitized === null ? "(nothing)" : highlighted(sanitized.slice(0, CAP), sanitizedRanges)}
              </pre>
            </div>

            {truncated ? (
              <p className="text-[11px] text-amber-300">
                Showing the first 200 KB of this delta.
              </p>
            ) : null}

            <ul className="flex flex-wrap gap-x-4 gap-y-1 border-t border-zinc-800 pt-2">
              {[...counts.entries()].map(([category, count]) => (
                <li key={category} className="flex items-center gap-1.5 text-[11px] text-slate-600">
                  <span aria-hidden className={`h-2 w-2 rounded ${CATEGORY_META[category].dot}`} />
                  <span>
                    {CATEGORY_META[category].label} ({count})
                  </span>
                </li>
              ))}
              {counts.size === 0 ? (
                <li className="text-[11px] font-medium text-slate-500">No changes in this event.</li>
              ) : null}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
