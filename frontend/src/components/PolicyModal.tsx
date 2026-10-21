import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import type { PolicySample } from "../types";

interface PolicyModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function PolicyModal({ isOpen, onClose }: PolicyModalProps) {
  const policy = useStore((state) => state.policy);
  const refreshAll = useStore((state) => state.refreshAll);

  const [sample, setSample] = useState<PolicySample | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!isOpen) {
      setSaveStatus(null);
      setCopied(false);
      return;
    }
    let active = true;
    setLoading(true);
    api
      .policySample()
      .then((data) => {
        if (active) setSample(data);
      })
      .catch((err) => {
        if (active) setSaveStatus(`Error loading sample: ${String(err)}`);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [isOpen]);

  if (!isOpen) return null;

  const onCopy = async () => {
    if (!sample?.yaml) return;
    try {
      await navigator.clipboard.writeText(sample.yaml);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      setSaveStatus("Failed to copy to clipboard.");
    }
  };

  const onDownload = () => {
    if (!sample?.yaml) return;
    const blob = new Blob([sample.yaml], { type: "text/yaml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = sample.filename || ".aishield";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const onSaveToWorkspace = async () => {
    setSaving(true);
    setSaveStatus(null);
    try {
      const res = await api.createSamplePolicy(true);
      setSaveStatus(res.message);
      await refreshAll();
      const updated = await api.policySample();
      setSample(updated);
    } catch (err) {
      setSaveStatus(`Error writing policy: ${String(err)}`);
    } finally {
      setSaving(false);
    }
  };

  const exists = sample?.exists ?? policy?.file_exists ?? false;
  const path = sample?.policy_path ?? policy?.policy_path ?? ".aishield";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm">
      <div className="relative flex max-h-[90vh] w-full max-w-3xl flex-col rounded-xl border border-zinc-800 bg-zinc-900 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-zinc-800 px-6 py-4">
          <div className="flex items-center gap-3">
            <span className="text-2xl" aria-hidden>
              🛡️
            </span>
            <div>
              <h2 className="text-lg font-bold text-zinc-100">
                Security Policy Configuration (.aishield)
              </h2>
              <p className="text-xs text-zinc-400">
                Declarative zero-trust runtime policy and privacy boundaries
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
            aria-label="Close modal"
          >
            ✕
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {/* Status Bar */}
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-zinc-300">File Status:</span>
                <span
                  className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${
                    exists
                      ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/30"
                      : "bg-amber-500/10 text-amber-400 border border-amber-500/30"
                  }`}
                >
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${
                      exists ? "bg-emerald-400" : "bg-amber-400"
                    }`}
                  />
                  {exists ? "Active on Disk" : "Not Found (Using Profile Defaults)"}
                </span>
              </div>
              <p className="font-mono text-xs text-zinc-500 truncate max-w-lg" title={path}>
                Path: {path}
              </p>
            </div>

            {policy ? (
              <div className="flex items-center gap-2">
                <span className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1 text-xs text-zinc-300">
                  Profile: <strong className="text-emerald-400">{policy.profile}</strong>
                </span>
                <span className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1 text-xs text-zinc-300">
                  Mode: <strong className="text-zinc-200">{policy.mode}</strong>
                </span>
              </div>
            ) : null}
          </div>

          {/* Quick Explanation */}
          <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3 text-xs text-zinc-300 space-y-1.5">
            <p>
              RepoShield enforces policy outside the AI agent&apos;s runtime using this{" "}
              <code className="text-emerald-400">.aishield</code> file.
            </p>
            <p className="text-zinc-400">
              Customize protected files, allowed commands, database tables, and egress domains for
              your specific project.
            </p>
          </div>

          {/* Status Message / Notification */}
          {saveStatus ? (
            <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs font-medium text-emerald-300">
              ✓ {saveStatus}
            </div>
          ) : null}

          {/* Code Viewer */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-zinc-400">Sample Template YAML</span>
              <span className="text-[11px] text-zinc-500">Read-only preview</span>
            </div>
            {loading ? (
              <div className="flex h-64 items-center justify-center rounded-lg border border-zinc-800 bg-zinc-950 text-xs text-zinc-400">
                Loading template…
              </div>
            ) : (
              <pre className="max-h-72 overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-950 p-4 font-mono text-xs leading-relaxed text-zinc-200 selection:bg-emerald-500/30">
                <code>{sample?.yaml || "# No template available."}</code>
              </pre>
            )}
          </div>
        </div>

        {/* Footer Actions */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-zinc-800 px-6 py-4">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onCopy}
              className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-700 hover:text-white transition"
            >
              {copied ? "✓ Copied!" : "📋 Copy YAML"}
            </button>
            <button
              type="button"
              onClick={onDownload}
              className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-700 hover:text-white transition"
            >
              ⬇️ Download .aishield
            </button>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onSaveToWorkspace}
              disabled={saving}
              className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 transition disabled:opacity-50"
            >
              {saving ? "Creating…" : "⚡ Create / Save in Project"}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-700"
            >
              Done
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
