import { useEffect, useRef, useState } from "react";

import { useStore } from "../store";
import type { ChainVerification } from "../types";
import { PolicyModal } from "./PolicyModal";

const STATUS_STYLE: Record<string, string> = {
  active: "bg-emerald-500/20 text-emerald-200 border-emerald-400/50",
  locked: "bg-rose-500/25 text-rose-200 border-rose-400/50",
  ended: "bg-slate-600/30 text-slate-300 border-slate-500/40",
};

export function Header() {
  const sessions = useStore((state) => state.sessions);
  const sessionId = useStore((state) => state.sessionId);
  const session = useStore((state) => state.session);
  const policy = useStore((state) => state.policy);
  const view = useStore((state) => state.view);
  const setView = useStore((state) => state.setView);
  const selectSession = useStore((state) => state.selectSession);
  const verifyLog = useStore((state) => state.verifyLog);
  const lockSession = useStore((state) => state.lockSession);
  const unlockSession = useStore((state) => state.unlockSession);
  const approvals = useStore((state) => state.approvals);

  const [banner, setBanner] = useState<ChainVerification | null>(null);
  const [busy, setBusy] = useState(false);
  const [policyModalOpen, setPolicyModalOpen] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const pending = approvals.filter(
    (approval) => !sessionId || approval.session_id === sessionId,
  ).length;

  useEffect(() => {
    document.title = pending > 0 ? `(${pending}) RepoShield` : "RepoShield";
    return () => {
      document.title = "RepoShield";
    };
  }, [pending]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const onVerify = async () => {
    setBusy(true);
    await verifyLog();
    setBusy(false);
    const result = useStore.getState().verify;
    setBanner(result);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setBanner(null), 10_000);
  };

  const status = session?.status ?? "active";
  const locked = status === "locked";

  return (
    <header className="border-b border-[#3b0d5c] bg-[#2a0845] px-5 py-3.5 text-white shadow-md">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2.5">
          <span aria-hidden className="flex h-7 w-7 items-center justify-center rounded-lg bg-amber-400/20 text-lg">
            🛡️
          </span>
          <span className="text-lg font-bold tracking-tight text-white">RepoShield AI</span>
        </div>

        <label className="sr-only" htmlFor="session-select">
          Session
        </label>
        <select
          id="session-select"
          value={sessionId ?? ""}
          onChange={(event) => void selectSession(event.target.value)}
          className="rounded-lg border border-white/20 bg-white/10 px-3 py-1.5 text-sm text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
        >
          {sessions.length === 0 ? <option value="" className="text-slate-900">no sessions</option> : null}
          {sessions.map((item) => (
            <option key={item.id} value={item.id} className="text-slate-900">
              {item.id} · {item.profile}
            </option>
          ))}
        </select>

        {policy ? (
          <button
            type="button"
            onClick={() => setPolicyModalOpen(true)}
            title="Click to view policy or generate .aishield template"
            className="group flex items-center gap-1.5 rounded-full border border-amber-400/60 bg-amber-400/20 px-3.5 py-1 text-xs text-amber-200 hover:bg-amber-400/30 transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 shadow-xs"
          >
            <span className="text-amber-300">🛡️ Policy:</span>
            <strong className="text-white group-hover:underline font-bold">
              {policy.profile}
            </strong>
            <span className="text-amber-300/60">·</span>
            <span className="text-amber-200">{policy.mode}</span>
            {policy.file_exists === false ? (
              <span className="ml-1 rounded bg-amber-500/30 px-1.5 py-0.2 text-[10px] text-amber-100 font-bold">
                no file
              </span>
            ) : null}
          </button>
        ) : null}

        <span
          className={`rounded-full border px-3 py-1 text-xs font-semibold tracking-wide ${
            STATUS_STYLE[status] ?? STATUS_STYLE.ended
          }`}
        >
          {status.toUpperCase()}
        </span>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div
            role="group"
            aria-label="Detail level"
            className="flex overflow-hidden rounded-lg border border-white/20 bg-white/10"
          >
            {(["simple", "detail"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                aria-pressed={view === mode}
                onClick={() => setView(mode)}
                className={`px-3 py-1.5 text-xs font-semibold capitalize transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 ${
                  view === mode
                    ? "bg-[#f59e0b] text-slate-950 font-bold"
                    : "text-white/80 hover:text-white"
                }`}
              >
                {mode}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={() => void onVerify()}
            disabled={busy}
            className="rounded-lg border border-amber-400/60 bg-amber-400/20 px-3 py-1.5 text-xs font-semibold text-amber-200 hover:bg-amber-400/30 transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 disabled:opacity-50 shadow-xs"
          >
            {busy ? "Verifying…" : "Verify Log"}
          </button>

          <button
            type="button"
            onClick={() => void (locked ? unlockSession() : lockSession())}
            disabled={status === "ended"}
            className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 disabled:opacity-40 shadow-xs ${
              locked
                ? "border-emerald-400/60 bg-emerald-500/20 text-emerald-200 hover:bg-emerald-500/30"
                : "border-rose-400/60 bg-rose-500/20 text-rose-200 hover:bg-rose-500/30"
            }`}
          >
            {locked ? "Unlock" : "Lock"}
          </button>
        </div>
      </div>

      {banner ? (
        <p
          role="status"
          className={`mt-3 rounded-xl border px-3.5 py-2 text-sm font-medium ${
            banner.ok
              ? "border-emerald-400/60 bg-emerald-500/20 text-emerald-100"
              : "border-rose-400/60 bg-rose-500/25 text-rose-100"
          }`}
        >
          {banner.ok
            ? `✓ Log intact, ${banner.checked} entries checked`
            : `Tampering detected at entry #${banner.first_bad_seq ?? "?"}`}
        </p>
      ) : null}

      <PolicyModal
        isOpen={policyModalOpen}
        onClose={() => setPolicyModalOpen(false)}
      />
    </header>
  );
}
