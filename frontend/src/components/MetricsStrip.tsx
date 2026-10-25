import { useStore } from "../store";

const CELLS = [
  { key: "ALLOW", label: "Allowed", dot: "bg-emerald-400" },
  { key: "MASK", label: "Masked", dot: "bg-amber-400" },
  { key: "HOLD", label: "Paused", dot: "bg-sky-400" },
  { key: "BLOCK", label: "Blocked", dot: "bg-red-400" },
] as const;

export function MetricsStrip() {
  const metrics = useStore((state) => state.metrics);

  return (
    <div className="rounded-2xl border border-slate-300 bg-white p-3.5 shadow-sm">
      <div className="grid grid-cols-4 gap-2">
        {CELLS.map((cell) => (
          <div key={cell.key} className="rounded-xl border border-slate-200 bg-slate-50/80 px-2.5 py-2">
            <div className="flex items-center gap-1.5">
              <span aria-hidden className={`h-2 w-2 rounded-full ${cell.dot}`} />
              <span className="truncate text-[10px] uppercase tracking-wide text-slate-600 font-bold">
                {cell.label}
              </span>
            </div>
            <p className="mt-0.5 text-lg font-bold tabular-nums text-[#2a0845]">
              {metrics?.counts?.[cell.key] ?? 0}
            </p>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-slate-500 font-medium">
        Mean pipeline latency{" "}
        <span className="tabular-nums text-slate-800 font-bold">
          {(metrics?.latency_mean_ms ?? 0).toFixed(1)} ms
        </span>
        <span className="text-slate-400"> · </span>
        p95{" "}
        <span className="tabular-nums text-slate-800 font-bold">
          {(metrics?.latency_p95_ms ?? 0).toFixed(1)} ms
        </span>
      </p>
    </div>
  );
}
