import { useEffect, useRef, useState } from "react";

import { useStore } from "../store";
import type { ChainVerification } from "../types";
import { PolicyModal } from "./PolicyModal";

const STATUS_STYLE: Record<string, string> = {
  active: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  locked: "bg-red-500/15 text-red-300 border-red-500/40",
  ended: "bg-zinc-600/20 text-zinc-300 border-zinc-500/40",
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
    <header className="border-b border-zinc-800 bg-zinc-950/90 px-4 py-3 backdrop-blur">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <span aria-hidden className="text-xl">
            🛡
          </span>
          <span className="text-lg font-semibold tracking-tight">RepoShield</span>
        </div>

        <label className="sr-only" htmlFor="session-select">
          Session
        </label>
        <select
          id="session-select"
          value={sessionId ?? ""}
          onChange={(event) => void selectSession(event.target.value)}
          className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
        >
          {sessions.length === 0 ? <option value="">no sessions</option> : null}
          {sessions.map((item) => (
            <option key={item.id} value={item.id}>
              {item.id} · {item.profile}
            </option>
          ))}
        </select>

        {policy ? (
          <button
            type="button"
            onClick={() => setPolicyModalOpen(true)}
            title="Click to view policy or generate .aishield template"
            className="group flex items-center gap-1.5 rounded-full border border-zinc-700 bg-zinc-900 px-3 py-1 text-xs text-zinc-300 hover:border-emerald-500/50 hover:bg-zinc-800 transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            <span>🛡️ Policy:</span>
            <strong className="text-emerald-400 group-hover:underline">
              {policy.profile}
            </strong>
            <span className="text-zinc-500">·</span>
            <span>{policy.mode}</span>
            {policy.file_exists === false ? (
              <span className="ml-1 rounded bg-amber-500/20 px-1 text-[10px] text-amber-300">
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
            className="flex overflow-hidden rounded-lg border border-zinc-700"
          >
            {(["simple", "detail"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                aria-pressed={view === mode}
                onClick={() => setView(mode)}
                className={`px-3 py-1.5 text-xs font-medium capitalize transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${
                  view === mode
                    ? "bg-zinc-700 text-white"
                    : "bg-zinc-900 text-zinc-400 hover:text-zinc-200"
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
            className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:border-emerald-500/60 hover:text-emerald-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 disabled:opacity-50"
          >
            {busy ? "Verifying…" : "Verify Log"}
          </button>

          <button
            type="button"
            onClick={() => void (locked ? unlockSession() : lockSession())}
            disabled={status === "ended"}
            className={`rounded-lg border px-3 py-1.5 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 disabled:opacity-40 ${
              locked
                ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20"
                : "border-red-500/50 bg-red-500/10 text-red-300 hover:bg-red-500/20"
            }`}
          >
            {locked ? "Unlock" : "Lock"}
          </button>
        </div>
      </div>

      {banner ? (
        <p
          role="status"
          className={`mt-3 rounded-lg border px-3 py-2 text-sm ${
            banner.ok
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
              : "border-red-500/40 bg-red-500/10 text-red-200"
          }`}
        >
          {banner.ok
            ? `Log intact, ${banner.checked} entries checked`
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
