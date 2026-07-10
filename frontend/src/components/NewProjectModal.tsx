import { useEffect, useRef, useState } from "react";
import { ApiError, createSession } from "../api";
import { LANGUAGE_OPTIONS } from "../languages";
import type { ModelPreset, SessionInfo } from "../types";
import {
  defaultModelPresetId,
  modelPresetDetail,
  selectableModelPresets,
} from "../modelPresets";

type Props = {
  open: boolean;
  modelPresets: ModelPreset[];
  onCancel: () => void;
  onCreated: (session: SessionInfo) => void;
};

const MISSING_CWD_PREFIX = "cwd does not exist:";

function missingCwdPath(err: unknown): string | null {
  if (
    err instanceof ApiError &&
    err.status === 400 &&
    err.detail?.startsWith(MISSING_CWD_PREFIX)
  ) {
    return err.detail.slice(MISSING_CWD_PREFIX.length).trim();
  }
  return null;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function NewProjectModal({
  open,
  modelPresets,
  onCancel,
  onCreated,
}: Props) {
  const [name, setName] = useState("");
  const [preferredLanguage, setPreferredLanguage] = useState("");
  const [modelPresetId, setModelPresetId] = useState("");
  const [concurrency, setConcurrency] = useState(1);
  const [cwd, setCwd] = useState("");
  const [temporary, setTemporary] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement | null>(null);
  const activeModelPresets = selectableModelPresets(modelPresets);

  useEffect(() => {
    if (open) {
      setName("");
      setPreferredLanguage("");
      setModelPresetId(defaultModelPresetId(modelPresets));
      setConcurrency(1);
      setCwd("");
      setTemporary(false);
      setSubmitting(false);
      setError(null);
      window.setTimeout(() => nameRef.current?.focus(), 0);
    }
  }, [open, modelPresets]);

  if (!open) return null;

  const submit = async () => {
    const cwdInput = cwd.trim();
    const payload = (createMissingCwd: boolean) => ({
      name: name.trim() || undefined,
      preferred_language: preferredLanguage || null,
      model_preset_id: modelPresetId || undefined,
      concurrency,
      cwd: temporary ? undefined : (cwdInput || undefined),
      temporary,
      create_missing_cwd: createMissingCwd,
    });

    setSubmitting(true);
    setError(null);
    try {
      const session = await createSession(payload(false));
      onCreated(session);
    } catch (err) {
      const missingPath = missingCwdPath(err);
      if (
        !temporary &&
        cwdInput &&
        missingPath &&
        window.confirm(
          [
            "Working directory does not exist:",
            missingPath,
            "",
            "Create it and continue?",
          ].join("\n"),
        )
      ) {
        try {
          const session = await createSession(payload(true));
          onCreated(session);
          return;
        } catch (retryErr) {
          setError(errorMessage(retryErr));
          setSubmitting(false);
          return;
        }
      }
      setError(errorMessage(err));
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-surface-scrim/60 backdrop-blur-sm">
      <div className="flex w-[480px] max-w-[95vw] flex-col rounded-xl border border-line bg-surface-raised shadow-modal">
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            <div className="font-display text-sm font-semibold text-ink-strong">
              New project
            </div>
            <div className="text-[11px] text-ink-muted">
              A project pins one git working tree; nodes are launched explicitly inside it.
            </div>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="rounded px-2 py-1 text-[11px] font-medium text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
          >
            Esc
          </button>
        </div>

        <div className="flex flex-col gap-4 px-5 py-4 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Name <span className="text-ink-subtle/70">(optional)</span>
            </span>
            <input
              ref={nameRef}
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My experiment"
              className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Language
            </span>
            <select
              value={preferredLanguage}
              onChange={(e) => setPreferredLanguage(e.target.value)}
              className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong focus:border-brand focus:outline-none"
            >
              {LANGUAGE_OPTIONS.map((option) => (
                <option key={option.value || "none"} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              模型档位
            </span>
            <select
              value={modelPresetId}
              onChange={(e) => setModelPresetId(e.target.value)}
              disabled={activeModelPresets.length === 0}
              className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong focus:border-brand focus:outline-none disabled:opacity-40"
            >
              {activeModelPresets.length === 0 && (
                <option value="">没有可用模型档位</option>
              )}
              {activeModelPresets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.label}
                </option>
              ))}
            </select>
            {modelPresetId && (
              <span className="text-[11px] text-ink-muted">
                {modelPresetDetail(modelPresets, modelPresetId) || modelPresetId}
              </span>
            )}
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Concurrency
            </span>
            <input
              type="number"
              min={1}
              step={1}
              value={concurrency}
              onChange={(event) => setConcurrency(Math.max(1, Number(event.target.value) || 1))}
              className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong focus:border-brand focus:outline-none"
            />
            <span className="text-[11px] text-ink-muted">
              Maximum nodes that may actually run at once. Extra work stays queued.
            </span>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Working directory{" "}
              <span className="text-ink-subtle/70">(optional)</span>
            </span>
            <input
              type="text"
              value={cwd}
              onChange={(e) => setCwd(e.target.value)}
              disabled={temporary}
              placeholder="leave blank to use server cwd"
              className="rounded-md border border-line bg-surface-sunken px-3 py-2 font-mono text-xs text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none disabled:opacity-40"
            />
          </label>

          <label className="flex items-start gap-2 text-xs text-ink">
            <input
              type="checkbox"
              checked={temporary}
              onChange={(e) => setTemporary(e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-brand"
            />
            <span>
              Temporary workspace
              <span className="ml-1 text-ink-muted">
                — server creates a fresh git workspace; ignores the path above.
              </span>
            </span>
          </label>

          {error && (
            <div className="rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-xs text-state-error">
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-line bg-surface-sunken px-5 py-3 rounded-b-xl">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="rounded-md border border-line bg-surface px-3 py-1.5 text-xs text-ink-muted transition hover:bg-surface-sunken hover:text-ink disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={submitting || !modelPresetId}
            onClick={() => void submit()}
            className="rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
          >
            {submitting ? "Creating…" : "Create project"}
          </button>
        </div>
      </div>
    </div>
  );
}
