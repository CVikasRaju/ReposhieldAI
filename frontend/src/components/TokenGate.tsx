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
    <div className="flex min-h-screen items-center justify-center bg-zinc-950 px-4 text-zinc-100">
      <form
        onSubmit={submit}
        className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-900/70 p-8 shadow-2xl"
      >
        <p className="text-xs font-semibold uppercase tracking-[0.3em] text-emerald-400">
          RepoShield
        </p>
        <h1 className="mt-3 text-2xl font-semibold">Approver access</h1>
        <p className="mt-2 text-sm text-zinc-400">
          Paste the approver token the gateway printed at startup. It is kept in memory
          for this tab only.
        </p>
        <label htmlFor="approver-token" className="mt-6 block text-sm font-medium text-zinc-300">
          Approver token
        </label>
        <input
          id="approver-token"
          type="password"
          autoFocus
          autoComplete="off"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          className="mt-2 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm text-zinc-100 outline-none focus-visible:border-emerald-400 focus-visible:ring-2 focus-visible:ring-emerald-400/50"
          placeholder="token"
        />
        {error ? (
          <p role="alert" className="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
            {error}
          </p>
        ) : null}
        <button
          type="submit"
          className="mt-6 w-full rounded-lg bg-emerald-500 px-4 py-2 font-semibold text-emerald-950 transition hover:bg-emerald-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300"
        >
          Open dashboard
        </button>
      </form>
    </div>
  );
}
