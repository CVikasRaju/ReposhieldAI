import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
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

  // Lock body scroll and listen for Escape key when open
  useEffect(() => {
    if (!isOpen) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);

    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen, onClose]);

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

  const modalContent = (
    <div
      className="fixed inset-0 z-[99999] flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm pointer-events-auto"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="policy-modal-title"
    >
      <div
        className="relative flex max-h-[90vh] w-full max-w-3xl flex-col rounded-2xl border-2 border-slate-300 bg-white text-slate-900 shadow-2xl opacity-100"
        style={{ backgroundColor: "#ffffff" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Deep Aubergine Header */}
        <div className="flex items-center justify-between rounded-t-2xl bg-[#2a0845] px-6 py-4.5 text-white">
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-amber-400/20 text-xl" aria-hidden>
              🛡️
            </span>
            <div>
              <h2 id="policy-modal-title" className="text-lg font-bold text-white tracking-wide">
                Security Policy Configuration (.aishield)
              </h2>
              <p className="text-xs text-amber-200/80 font-medium">
                Declarative zero-trust runtime policy and privacy boundaries
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/10 text-white hover:bg-white/20 hover:text-amber-300 transition"
            aria-label="Close modal"
          >
            ✕
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4 bg-white">
          {/* Status Bar */}
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3.5 shadow-sm">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-slate-700">File Status:</span>
                <span
                  className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                    exists
                      ? "bg-emerald-100 text-emerald-800 border border-emerald-300"
                      : "bg-amber-100 text-amber-800 border border-amber-300"
                  }`}
                >
                  <span
                    className={`h-2 w-2 rounded-full ${
                      exists ? "bg-emerald-500" : "bg-amber-500 animate-pulse"
                    }`}
                  />
                  {exists ? "Active on Disk" : "Not Found (Using Profile Defaults)"}
                </span>
              </div>
              <p className="font-mono text-xs text-slate-500 truncate max-w-lg" title={path}>
                Path: {path}
              </p>
            </div>

            {policy ? (
              <div className="flex items-center gap-2">
                <span className="rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs text-slate-700 shadow-xs">
                  Profile: <strong className="text-[#2a0845] font-bold">{policy.profile}</strong>
                </span>
                <span className="rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs text-slate-700 shadow-xs">
                  Mode: <strong className="text-amber-700 font-bold">{policy.mode}</strong>
                </span>
              </div>
            ) : null}
          </div>

          {/* Quick Explanation */}
          <div className="rounded-xl border border-amber-200 bg-amber-50/70 p-3.5 text-xs text-amber-950 space-y-1">
            <p className="font-semibold text-amber-900">
              RepoShield enforces policy outside the AI agent&apos;s runtime using this{" "}
              <code className="rounded bg-amber-200/60 px-1 py-0.5 font-mono text-amber-950 font-bold">.aishield</code> file.
            </p>
            <p className="text-slate-600">
              Customize protected files, allowed commands, database tables, and egress domains for your specific project.
            </p>
          </div>

          {/* Status Message / Notification */}
          {saveStatus ? (
            <div className="rounded-xl border border-emerald-300 bg-emerald-50 p-3.5 text-xs font-semibold text-emerald-900 shadow-xs">
              ✓ {saveStatus}
            </div>
          ) : null}

          {/* Code Viewer */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-slate-700 uppercase tracking-wider">Sample Template YAML</span>
              <span className="text-[11px] text-slate-500 font-medium">Read-only preview</span>
            </div>
            {loading ? (
              <div className="flex h-64 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-xs text-slate-500">
                Loading template…
              </div>
            ) : (
              <pre className="max-h-72 overflow-y-auto rounded-xl border border-slate-800 bg-slate-900 p-4 font-mono text-xs leading-relaxed text-amber-300 selection:bg-amber-500/30 shadow-inner">
                <code>{sample?.yaml || "# No template available."}</code>
              </pre>
            )}
          </div>
        </div>

        {/* Footer Actions */}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-b-2xl border-t border-slate-200 bg-slate-50 px-6 py-4">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onCopy}
              className="rounded-lg border border-slate-300 bg-white px-3.5 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-100 hover:text-slate-950 transition shadow-xs"
            >
              {copied ? "✓ Copied!" : "📋 Copy YAML"}
            </button>
            <button
              type="button"
              onClick={onDownload}
              className="rounded-lg border border-slate-300 bg-white px-3.5 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-100 hover:text-slate-950 transition shadow-xs"
            >
              ⬇️ Download .aishield
            </button>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onSaveToWorkspace}
              disabled={saving}
              className="rounded-lg bg-[#f59e0b] px-4 py-1.5 text-xs font-bold text-slate-950 hover:bg-[#d97706] transition shadow-xs disabled:opacity-50"
            >
              {saving ? "Creating…" : "⚡ Create / Save in Project"}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg bg-[#2a0845] px-4 py-1.5 text-xs font-semibold text-white hover:bg-[#3b0764] transition shadow-xs"
            >
              Done
            </button>
          </div>
        </div>
      </div>
    </div>
  );

  return createPortal(modalContent, document.body);
}