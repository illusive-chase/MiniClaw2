import { useEffect, useState } from "react";
import { listTemplates, runTemplate } from "../api";
import type { ModelPreset, TemplateSummary, SessionInfo } from "../types";
import { modelPresetDetail, modelPresetLabel } from "../modelPresets";

type Props = {
  modelPresets: ModelPreset[];
  onLaunched: (session: SessionInfo, templateName: string) => void;
};

export function TestsPanel({ modelPresets, onLaunched }: Props) {
  const [templates, setTemplates] = useState<TemplateSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [launching, setLaunching] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listTemplates()
      .then((next) => {
        if (!cancelled) setTemplates(next);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const onRun = async (name: string, modelPresetId: string) => {
    const key = `${name}:${modelPresetId}`;
    setLaunching(key);
    setError(null);
    try {
      const session = await runTemplate(name, modelPresetId);
      onLaunched(session, name);
    } catch (err) {
      setError(String(err));
    } finally {
      setLaunching(null);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-surface px-6 py-6 text-ink">
      <div className="mb-6 max-w-2xl">
        <h1 className="font-display text-lg font-semibold tracking-tight text-ink-strong">
          Tests
        </h1>
        <p className="mt-1 text-xs leading-relaxed text-ink-muted">
          Each template runs in a fresh temporary git workspace and opens as a
          normal virtual-node lane. Use the regular canvas controls to promote
          work, inspect verifier results, and complete human-review steps.
        </p>
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-xs text-state-error">
          {error}
        </div>
      )}

      {templates === null && !error && (
        <div className="text-xs text-ink-muted">Loading…</div>
      )}

      {templates && templates.length === 0 && (
        <div className="text-xs text-ink-muted">No templates are bundled.</div>
      )}

      <div className="grid max-w-3xl grid-cols-1 gap-3">
        {templates?.map((s) => (
          <div
            key={s.name}
            className="rounded-lg border border-line bg-surface-raised px-4 py-3 shadow-card transition hover:border-line-strong"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="font-mono text-sm text-ink-strong">{s.name}</div>
                <div className="mt-1 text-[11px] text-ink-muted">{s.brief}</div>
                <div className="mt-2 flex items-center gap-2 text-[10px] uppercase tracking-[0.14em] text-ink-subtle">
                  <span>
                    {s.node_count} node{s.node_count === 1 ? "" : "s"}
                  </span>
                  {s.auto_commit && (
                    <>
                      <span className="text-line-strong">·</span>
                      <span>auto-commit</span>
                    </>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                {s.allowed_model_preset_ids.map((modelPresetId) => (
                  <button
                    key={modelPresetId}
                    type="button"
                    onClick={() =>
                      void onRun(s.name, modelPresetId)
                    }
                    disabled={launching !== null}
                    title={modelPresetDetail(modelPresets, modelPresetId) || undefined}
                    className="inline-flex h-8 items-center rounded-md border border-line bg-surface px-3 text-xs text-ink transition hover:border-brand/40 hover:bg-brand-soft hover:text-brand-ink disabled:opacity-40"
                  >
                    {launching === `${s.name}:${modelPresetId}`
                      ? "启动中…"
                      : `运行 · ${modelPresetLabel(modelPresets, modelPresetId)}`}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
