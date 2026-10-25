import { useEffect, useState } from "react";

import { ActivityFeed } from "./components/ActivityFeed";
import { ApprovalCard } from "./components/ApprovalCard";
import { DeltaView } from "./components/DeltaView";
import { Header } from "./components/Header";
import { InterventionLog } from "./components/InterventionLog";
import { KnowledgeGraph } from "./components/KnowledgeGraph";
import { MetricsStrip } from "./components/MetricsStrip";
import { TokenGate } from "./components/TokenGate";
import { useStore } from "./store";

/** Read the token from the hash once, then clear it from the address bar. */
function readTokenFromHash(): string | null {
  const match = /(?:^#|&)token=([^&]+)/.exec(window.location.hash);
  if (!match) return null;
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${window.location.search}`,
  );
  return decodeURIComponent(match[1]);
}

export default function App() {
  const [token, setToken] = useState<string | null>(() => readTokenFromHash());

  const hydrate = useStore((state) => state.hydrate);
  const disconnect = useStore((state) => state.disconnect);
  const setStoreToken = useStore((state) => state.setToken);
  const authError = useStore((state) => state.authError);
  const connection = useStore((state) => state.connection);
  const error = useStore((state) => state.error);
  const loading = useStore((state) => state.loading);
  const approvals = useStore((state) => state.approvals);
  const sessionId = useStore((state) => state.sessionId);
  const refreshAll = useStore((state) => state.refreshAll);
  const setError = useStore((state) => state.setError);

  useEffect(() => {
    if (!token) return;
    setStoreToken(token);
    void hydrate(token);
    return () => disconnect();
  }, [token, hydrate, disconnect, setStoreToken]);

  if (!token) {
    return <TokenGate error={authError} onSubmit={setToken} />;
  }

  const pending = approvals.filter(
    (approval) => !sessionId || approval.session_id === sessionId,
  );

  return (
    <div className="min-h-screen bg-[#eef2f6] text-[#1e1035]">
      <Header />

      {connection === "reconnecting" ? (
        <div
          role="status"
          className="border-b border-amber-300 bg-amber-50 px-4 py-1.5 text-xs font-semibold text-amber-900"
        >
          Reconnecting to the gateway…
        </div>
      ) : null}

      {error ? (
        <div
          role="alert"
          className="flex items-center gap-3 border-b border-rose-300 bg-rose-50 px-4 py-1.5 text-xs text-rose-900"
        >
          <span>{error}</span>
          <button
            type="button"
            onClick={() => {
              setError(null);
              void refreshAll();
            }}
            className="rounded border border-rose-400 px-2 py-0.5 font-bold hover:bg-rose-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-400"
          >
            Retry
          </button>
        </div>
      ) : null}

      <main className="mx-auto grid max-w-[1600px] gap-3 p-3 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="order-2 flex min-h-[320px] flex-col lg:order-1">
          <div className="flex h-full min-h-[320px] flex-col">
            {loading && !sessionId ? (
              <div className="flex flex-1 items-center justify-center rounded-2xl border border-slate-300 bg-white text-sm text-slate-500 shadow-sm">
                Loading workspace…
              </div>
            ) : (
              <KnowledgeGraph />
            )}
          </div>
        </div>

        <aside className="order-1 flex flex-col gap-2.5 lg:order-2">
          <div className="flex items-center justify-between rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 shadow-sm">
            <h2 className="text-sm font-bold text-[#2a0845]">Approvals</h2>
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-bold text-amber-900">
              {pending.length} waiting
            </span>
          </div>
          <div className="flex max-h-[420px] min-h-[80px] flex-col gap-2 overflow-y-auto">
            {pending.length === 0 ? (
              <p className="rounded-xl border border-slate-200 bg-white px-3 py-6 text-sm text-slate-500 shadow-xs text-center">
                Nothing waiting for approval.
              </p>
            ) : (
              pending.map((approval) => (
                <ApprovalCard key={approval.id} approval={approval} />
              ))
            )}
          </div>
          <MetricsStrip />
        </aside>

        <div className="order-3 flex min-h-[240px] flex-col lg:order-3">
          <ActivityFeed />
        </div>
        <div className="order-4 flex min-h-[260px] flex-col lg:order-4">
          <DeltaView />
        </div>

        <div className="order-5 lg:col-span-2">
          <InterventionLog />
        </div>
      </main>
    </div>
  );
}
