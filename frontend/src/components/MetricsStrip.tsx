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
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-3">
      <div className="grid grid-cols-4 gap-2">
        {CELLS.map((cell) => (
          <div key={cell.key} className="rounded-lg bg-zinc-950/60 px-2 py-1.5">
            <div className="flex items-center gap-1.5">
              <span aria-hidden className={`h-2 w-2 rounded-full ${cell.dot}`} />
              <span className="truncate text-[10px] uppercase tracking-wide text-zinc-400">
                {cell.label}
              </span>
            </div>
            <p className="mt-0.5 text-lg font-semibold tabular-nums text-zinc-100">
              {metrics?.counts?.[cell.key] ?? 0}
            </p>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-zinc-400">
        Mean pipeline latency{" "}
        <span className="tabular-nums text-zinc-200">
          {(metrics?.latency_mean_ms ?? 0).toFixed(1)} ms
        </span>
        <span className="text-zinc-600"> · </span>
        p95{" "}
        <span className="tabular-nums text-zinc-200">
          {(metrics?.latency_p95_ms ?? 0).toFixed(1)} ms
        </span>
      </p>
    </div>
  );
}
