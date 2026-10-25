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
        className="w-full max-w-md rounded-3xl border-2 border-slate-300 bg-white p-8 shadow-2xl"
      >
        <p className="text-xs font-black uppercase tracking-[0.3em] text-[#3b0764]">
          RepoShield AI
        </p>
        <h1 className="mt-3 text-2xl font-bold text-[#2a0845]">Approver Access</h1>
        <p className="mt-2 text-sm text-slate-500">
          Paste the approver token to authenticate this session. Every human dashboard and approval channel requires verification.
        </p>

        <label htmlFor="approver-token" className="mt-6 block text-sm font-bold text-slate-700">
          Approver Token
        </label>
        <input
          id="approver-token"
          type="text"
          autoFocus
          autoComplete="off"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          className="mt-2 w-full rounded-xl border border-slate-300 bg-slate-50 px-3.5 py-2.5 font-mono text-sm text-slate-900 shadow-inner outline-none focus-visible:border-amber-400 focus-visible:ring-2 focus-visible:ring-amber-400"
          placeholder="e.g. reposhield-approver-token"
        />

        <p className="mt-2 text-xs text-slate-500">
          Default token:{" "}
          <button
            type="button"
            onClick={() => setValue("reposhield-approver-token")}
            className="rounded bg-amber-100 px-1.5 py-0.5 font-mono font-bold text-amber-900 hover:bg-amber-200 transition cursor-pointer"
            title="Click to fill default demo token"
          >
            reposhield-approver-token
          </button>
        </p>

        {error ? (
          <p role="alert" className="mt-3 rounded-xl border border-rose-300 bg-rose-50 px-3 py-2 text-sm font-semibold text-rose-800">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          className="mt-6 w-full rounded-xl bg-[#2a0845] px-4 py-2.5 font-bold text-white transition hover:bg-[#3b0764] shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 cursor-pointer"
        >
          Open Dashboard
        </button>
      </form>
    </div>
  );
}
