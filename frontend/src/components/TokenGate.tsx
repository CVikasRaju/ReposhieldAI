import { useState, type FormEvent } from "react";

export function TokenGate({
  error,
  onSubmit,
}: {
  error: string | null;
  onSubmit: (token: string) => void;
}) {
  const [value, setValue] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const token = value.trim();
    if (token) onSubmit(token);
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#eef2f6] px-4 text-[#1e1035]">
      <form
        onSubmit={submit}
        className="w-full max-w-md rounded-2xl border-2 border-slate-300 bg-white p-8 shadow-2xl rounded-3xl"
      >
        <p className="text-xs font-semibold uppercase tracking-[0.3em] text-[#3b0764] font-black tracking-widest">
          RepoShield
        </p>
        <h1 className="mt-3 text-2xl font-bold text-[#2a0845]">Approver access</h1>
        <p className="mt-2 text-sm text-slate-500">
          Paste the approver token the gateway printed at startup. It is kept in memory
          for this tab only.
        </p>
        <label htmlFor="approver-token" className="mt-6 block text-sm font-medium text-slate-700 font-semibold">
          Approver token
        </label>
        <input
          id="approver-token"
          type="password"
          autoFocus
          autoComplete="off"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          className="mt-2 w-full rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-sm text-slate-900 shadow-inner outline-none focus-visible:border-emerald-400 focus-visible:ring-2 focus-visible:ring-emerald-400/50"
          placeholder="token"
        />
        {error ? (
          <p role="alert" className="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
            {error}
          </p>
        ) : null}
        <button
          type="submit"
          className="mt-6 w-full rounded-lg bg-[#2a0845] px-4 py-2.5 font-bold text-white transition hover:bg-[#3b0764] shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"
        >
          Open dashboard
        </button>
      </form>
    </div>
  );
}
