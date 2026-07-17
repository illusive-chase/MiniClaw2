import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { LANGUAGE_OPTIONS } from "../languages";
import {
  defaultModelPresetId,
  modelPresetDetail,
  modelPresetLabel,
  selectableModelPresets,
} from "../modelPresets";
import type {
  ContextSpaceBindingSummary,
  ContextSpacePlugSummary,
  ModelPreset,
  PlanspaceMode,
  SessionContextSpaceInfo,
  SessionInfo,
} from "../types";

export type ProjectPanelProps = {
  session: SessionInfo | null;
  modelPresets: ModelPreset[];
  contextSpace: SessionContextSpaceInfo | null;
  contextSpaceLoading: boolean;
  contextSpaceSaving: boolean;
  contextSpaceError: string | null;
  settingsSaving: boolean;
  settingsError: string | null;
  onActivatePlanspace: (binding_id: string, planspace_id: string) => void;
  onSelectContextBinding: (binding_id: string) => void;
  onPreferredLanguageChange: (preferredLanguage: string | null) => void;
  onConcurrencyChange: (concurrency: number) => void;
  onNewDirection: (
    userSeed: string,
    mode: PlanspaceMode,
    modelPresetId: string,
  ) => void;
  onStartBlankDirection: (
    userSeed: string,
    mode: PlanspaceMode,
    modelPresetId: string,
  ) => void;
  onNewPrinciple?: (userSeed: string) => Promise<void> | void;
  onImportSkill?: (source: string) => Promise<void> | void;
  onContextInit: () => void;
  onContextRefresh: () => void;
  onContextCancel: () => void;
  onTogglePlanspaceVisibility: (planspaceId: string, hidden: boolean) => void;
  newDirectionRequestVersion: number;
  onNewDirectionRequestHandled: () => void;
};

/**
 * Side panel when the project root is selected.
 *
 * Project-root actions are concierge-style: creating a direction launches the
 * bootstrap agent node, while CONTEXT.md init/refresh stay out of the timeline.
 */
export function ProjectPanel({
  session,
  modelPresets,
  contextSpace,
  contextSpaceLoading,
  contextSpaceSaving,
  contextSpaceError,
  settingsSaving,
  settingsError,
  onActivatePlanspace,
  onSelectContextBinding,
  onPreferredLanguageChange,
  onConcurrencyChange,
  onNewDirection,
  onStartBlankDirection,
  onNewPrinciple,
  onImportSkill,
  onContextInit,
  onContextRefresh,
  onContextCancel,
  onTogglePlanspaceVisibility,
  newDirectionRequestVersion,
  onNewDirectionRequestHandled,
}: ProjectPanelProps) {
  const [composerOpen, setComposerOpen] = useState(false);
  const [seed, setSeed] = useState("");
  const [newDirectionMode, setNewDirectionMode] = useState<PlanspaceMode>("manual");
  const [newDirectionModelPresetId, setNewDirectionModelPresetId] = useState("");
  const seedRef = useRef<HTMLTextAreaElement | null>(null);
  const lastRequestVersionRef = useRef(0);
  const [principleOpen, setPrincipleOpen] = useState(false);
  const [principleSeed, setPrincipleSeed] = useState("");
  const [principleBusy, setPrincipleBusy] = useState(false);
  const [principleError, setPrincipleError] = useState<string | null>(null);
  const principleSeedRef = useRef<HTMLTextAreaElement | null>(null);
  const [skillSource, setSkillSource] = useState("");
  const [skillBusy, setSkillBusy] = useState(false);
  const [skillError, setSkillError] = useState<string | null>(null);

  const activeBinding = contextSpace?.bindings.find(
    (b) => b.id === (contextSpace?.resolved_binding_id ?? session?.project_context_binding_id),
  );
  const selectableBindings = contextSpace?.selectable_bindings ?? contextSpace?.bindings ?? [];
  const directions = useMemo(
    () => collectDirections(activeBinding),
    [activeBinding],
  );
  const notesExist = !!contextSpace?.context_file?.exists;
  const refreshing = !!contextSpace?.context_refresh?.running;
  const busy = contextSpaceSaving || refreshing || settingsSaving || !!session?.read_only;
  const activeModelPresets = selectableModelPresets(modelPresets);

  useEffect(() => {
    setNewDirectionModelPresetId(
      defaultModelPresetId(modelPresets, session?.model_preset_id),
    );
  }, [modelPresets, session?.id, session?.model_preset_id]);

  useEffect(() => {
    if (newDirectionRequestVersion <= 0) {
      lastRequestVersionRef.current = 0;
      return;
    }
    if (newDirectionRequestVersion === lastRequestVersionRef.current) return;
    lastRequestVersionRef.current = newDirectionRequestVersion;
    setComposerOpen(true);
    window.setTimeout(() => seedRef.current?.focus(), 30);
    onNewDirectionRequestHandled();
  }, [newDirectionRequestVersion, onNewDirectionRequestHandled]);

  if (!session) {
    return (
      <div className="flex h-full items-center justify-center px-4 text-sm text-ink-muted">
        No project selected.
      </div>
    );
  }

  const submitNewDirection = (kind: "concierge" | "blank") => {
    const trimmed = seed.trim();
    if (!trimmed || busy) return;
    const modelPresetId = defaultModelPresetId(
      modelPresets,
      newDirectionModelPresetId || session.model_preset_id,
    );
    if (!modelPresetId) return;
    if (kind === "concierge") {
      onNewDirection(trimmed, newDirectionMode, modelPresetId);
    } else {
      onStartBlankDirection(trimmed, newDirectionMode, modelPresetId);
    }
    setSeed("");
    setNewDirectionMode("manual");
    setNewDirectionModelPresetId(defaultModelPresetId(modelPresets, session.model_preset_id));
    setComposerOpen(false);
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-line bg-surface-raised px-4 py-3">
        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          Project
        </div>
        <h2 className="mt-1 truncate font-display text-[15px] font-semibold leading-snug text-ink-strong">
          {session.name?.trim() || `Project ${session.id.slice(0, 8)}`}
        </h2>
        <div className="mt-1 font-mono text-[10.5px] text-ink-muted">
          {session.id}
        </div>
        {session.read_only && (
          <div className="mt-2 inline-flex rounded border border-state-waiting/40 bg-state-waiting-soft px-2 py-1 text-[10.5px] text-state-waiting">
            read-only · native to {session.native_machine_label} · as of {session.last_sync_at ? new Date(session.last_sync_at * 1000).toLocaleString() : "never synced"}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto bg-surface px-4 py-3 text-sm">
        <section className="mb-5">
          <SectionLabel>Settings</SectionLabel>
          <dl className="mt-1 grid grid-cols-[140px_1fr] gap-x-3 gap-y-1.5 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px]">
            <KV
              label="Default model"
              value={modelPresetLabel(modelPresets, session.model_preset_id)}
            />
            <KV label="Temporary" value={session.temporary ? "yes" : "no"} />
            <KV label="Template" value={session.template_id ?? "(none)"} />
            <KV label="Turns" value={String(session.turns)} />
            <KV
              label="Created"
              value={new Date(session.created_at * 1000).toLocaleString()}
            />
          </dl>
          <label className="mt-2 flex items-center justify-between gap-3 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px]">
            <span className="text-ink-subtle">Preferred language</span>
            <select
              value={session.preferred_language ?? ""}
              disabled={busy}
              onChange={(event) => {
                onPreferredLanguageChange(event.target.value || null);
              }}
              className="min-w-[190px] rounded border border-line bg-surface px-2 py-1 text-[11.5px] text-ink-strong focus:border-brand focus:outline-none disabled:opacity-50"
            >
              {LANGUAGE_OPTIONS.map((option) => (
                <option key={option.value || "none"} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="mt-2 flex items-center justify-between gap-3 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px]">
            <span className="text-ink-subtle">Concurrency</span>
            <input
              type="number"
              min={1}
              step={1}
              value={session.concurrency}
              disabled={busy}
              onChange={(event) => {
                onConcurrencyChange(Math.max(1, Number(event.target.value) || 1));
              }}
              className="w-20 rounded border border-line bg-surface px-2 py-1 text-right text-[11.5px] text-ink-strong focus:border-brand focus:outline-none disabled:opacity-50"
            />
          </label>
          <div className="mt-2 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px] text-ink-subtle">
            {session.active_count} active · {session.queued_count} queued · limit {session.concurrency}
          </div>
          {settingsError && (
            <div className="mt-2 rounded-md border border-state-error/30 bg-state-error-soft p-2 text-xs text-state-error">
              {settingsError}
            </div>
          )}
        </section>

        <section className="mb-5">
          <div className="flex items-baseline justify-between">
            <SectionLabel>Project actions</SectionLabel>
            {contextSpaceLoading && (
              <span className="text-[10px] text-ink-subtle">loading...</span>
            )}
          </div>
          {contextSpaceError && (
            <div className="mt-1 rounded-md border border-state-error/30 bg-state-error-soft p-2 text-xs text-state-error">
              {contextSpaceError}
            </div>
          )}
          {refreshing && (
            <div className="mt-2 flex items-center justify-between gap-2 rounded-md border border-state-running/30 bg-state-running-soft px-3 py-2 text-[11.5px] text-state-running">
              <span>
                {contextSpace?.context_refresh?.mode === "init"
                  ? "Drafting project notes..."
                  : "Refreshing project notes..."}
              </span>
              <button
                type="button"
                onClick={onContextCancel}
                className="rounded-sm border border-state-running/40 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-state-running transition hover:bg-state-running/10"
              >
                Cancel
              </button>
            </div>
          )}
          <div className="mt-2 grid grid-cols-1 gap-2">
            <button
              type="button"
              onClick={() => setComposerOpen((v) => !v)}
              disabled={busy}
              className="rounded-md bg-brand px-3 py-2 text-left text-[12px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
            >
              + New direction
            </button>
            <button
              type="button"
              onClick={notesExist ? onContextRefresh : onContextInit}
              disabled={busy}
              className="rounded-md border border-line bg-surface-raised px-3 py-2 text-left text-[12px] text-ink transition hover:border-line-strong disabled:opacity-40"
            >
              {notesExist ? "Refresh project notes" : "Initialize project notes"}
            </button>
            {onNewPrinciple && (
              <button
                type="button"
                onClick={() => {
                  setPrincipleOpen((v) => !v);
                  window.setTimeout(() => principleSeedRef.current?.focus(), 30);
                }}
                disabled={busy}
                className="rounded-md border border-line bg-surface-raised px-3 py-2 text-left text-[12px] text-ink transition hover:border-line-strong disabled:opacity-40"
              >
                + New principle
              </button>
            )}
            {onImportSkill && (
              <form
                className="flex gap-2"
                onSubmit={async (event) => {
                  event.preventDefault();
                  const source = skillSource.trim();
                  if (!source) return;
                  setSkillBusy(true);
                  setSkillError(null);
                  try {
                    await onImportSkill(source);
                    setSkillSource("");
                  } catch (error) {
                    setSkillError(String(error));
                  } finally {
                    setSkillBusy(false);
                  }
                }}
              >
                <input
                  value={skillSource}
                  onChange={(event) => setSkillSource(event.target.value)}
                  placeholder="Skill path, zip, or git URL"
                  className="min-w-0 flex-1 rounded-md border border-line bg-surface px-2 py-1.5 text-[11px] text-ink"
                />
                <button
                  type="submit"
                  disabled={busy || skillBusy || !skillSource.trim()}
                  className="rounded-md border border-state-review/50 bg-state-review/10 px-2 py-1.5 text-[11px] text-ink disabled:opacity-40"
                >
                  {skillBusy ? "Importing…" : "Import skill"}
                </button>
              </form>
            )}
          </div>

          {skillError && (
            <div className="mt-2 rounded-md border border-state-error/30 bg-state-error-soft p-2 text-[11px] text-state-error">
              {skillError}
            </div>
          )}

          {principleOpen && onNewPrinciple && (
            <div className="mt-3 rounded-md border border-line bg-surface-sunken p-3">
              <label className="block text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
                What behavior should this principle shape?
              </label>
              <textarea
                ref={principleSeedRef}
                value={principleSeed}
                onChange={(e) => setPrincipleSeed(e.target.value)}
                rows={4}
                placeholder="e.g. review discipline, testing standards, or house style…"
                className="mt-1 w-full resize-none rounded-md border border-line bg-surface px-3 py-2 text-[13px] leading-relaxed text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
              />
              {principleError && (
                <div className="mt-2 rounded-md border border-state-error/30 bg-state-error-soft p-2 text-[11px] text-state-error">
                  {principleError}
                </div>
              )}
              <div className="mt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setPrincipleOpen(false);
                    setPrincipleSeed("");
                    setPrincipleError(null);
                  }}
                  className="rounded border border-line bg-surface px-2.5 py-1 text-[11px] text-ink-muted hover:text-ink"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={principleBusy || !principleSeed.trim()}
                  onClick={async () => {
                    const trimmed = principleSeed.trim();
                    if (!trimmed) return;
                    setPrincipleBusy(true);
                    setPrincipleError(null);
                    try {
                      await onNewPrinciple(trimmed);
                      setPrincipleOpen(false);
                      setPrincipleSeed("");
                    } catch (err) {
                      setPrincipleError(String(err));
                    } finally {
                      setPrincipleBusy(false);
                    }
                  }}
                  className="rounded-md bg-brand px-2.5 py-1 text-[11.5px] font-medium text-white disabled:opacity-40"
                >
                  {principleBusy ? "Creating…" : "Create principle draft"}
                </button>
              </div>
            </div>
          )}

          {composerOpen && (
            <div className="mt-3 rounded-md border border-line bg-surface-sunken p-3">
              <label className="block text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
                Direction
              </label>
              <textarea
                ref={seedRef}
                value={seed}
                onChange={(event) => setSeed(event.target.value)}
                rows={5}
                placeholder="What direction are you taking? A paragraph is fine."
                className="mt-1 w-full resize-none rounded-md border border-line bg-surface px-3 py-2 text-[13px] leading-relaxed text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
              />
              <div className="mt-2 inline-flex rounded-md border border-line bg-surface p-0.5">
                {([
                  ["manual", "Wait for me to promote"],
                  ["auto", "Auto-promote when ready"],
                ] as const).map(([mode, label]) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setNewDirectionMode(mode)}
                    className={
                      "rounded px-2.5 py-1 text-[10.5px] font-medium transition " +
                      (newDirectionMode === mode
                        ? "bg-surface-raised text-ink-strong shadow-card"
                        : "text-ink-muted hover:text-ink")
                    }
                  >
                    {label}
                  </button>
                ))}
              </div>
              <label className="mt-2 flex items-center justify-between gap-3 rounded-md border border-line bg-surface px-3 py-2 text-[11.5px]">
                <span className="text-ink-subtle">Model preset</span>
                <select
                  value={newDirectionModelPresetId}
                  onChange={(event) =>
                    setNewDirectionModelPresetId(event.target.value)
                  }
                  className="min-w-[170px] rounded border border-line bg-surface-sunken px-2 py-1 text-[11.5px] text-ink-strong focus:border-brand focus:outline-none"
                >
                  {activeModelPresets.map((preset) => (
                    <option key={preset.id} value={preset.id}>
                      {preset.label}
                    </option>
                  ))}
                </select>
              </label>
              <div className="mt-1 pl-3 text-[10.5px] text-ink-subtle">
                {modelPresetDetail(modelPresets, newDirectionModelPresetId)}
              </div>
              <div className="mt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setComposerOpen(false)}
                  className="rounded border border-line bg-surface px-2.5 py-1 text-[11px] text-ink-muted hover:text-ink"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={busy || seed.trim().length === 0 || !newDirectionModelPresetId}
                  onClick={() => submitNewDirection("concierge")}
                  className="rounded-md bg-brand px-2.5 py-1 text-[11px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Draft with concierge
                </button>
                <button
                  type="button"
                  disabled={busy || seed.trim().length === 0 || !newDirectionModelPresetId}
                  onClick={() => submitNewDirection("blank")}
                  title="Skip the concierge - start with one empty virtual you'll fill in yourself."
                  className="rounded-md border border-line-strong bg-surface-raised px-2.5 py-1 text-[11px] font-medium text-ink transition hover:border-brand hover:text-ink-strong disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Start blank
                </button>
              </div>
            </div>
          )}
        </section>

        <section className="mb-5">
          <SectionLabel>Directions</SectionLabel>
          {!contextSpace ? (
            <div className="mt-1 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px] text-ink-muted">
              Directions not loaded.
            </div>
          ) : directions.length === 0 ? (
            <div className="mt-1 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px] text-ink-muted">
              This project has no directions yet. Start one to give the agent a
              notebook of plans and decisions.
            </div>
          ) : (
            <ul className="mt-1 space-y-1">
              {directions.map((item) => (
                <li key={item.plug.id}>
                  <DirectionRow
                    binding={item.binding}
                    plug={item.plug}
                    saving={busy}
                    onActivatePlanspace={onActivatePlanspace}
                    onTogglePlanspaceVisibility={onTogglePlanspaceVisibility}
                  />
                </li>
              ))}
            </ul>
          )}

          {contextSpace && !activeBinding && selectableBindings.length > 0 && (
            <div className="mt-4">
              <SectionLabel>Existing memory profiles</SectionLabel>
              <ul className="mt-1 space-y-1">
                {selectableBindings.map((binding) => (
                  <li key={binding.id}>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => onSelectContextBinding(binding.id)}
                      className="block w-full rounded-md border border-line bg-surface-raised px-3 py-2 text-left text-[12px] transition hover:border-line-strong disabled:opacity-50"
                    >
                      <div className="line-clamp-1 font-medium text-ink-strong">
                        {binding.title}
                      </div>
                      <div className="mt-0.5 font-mono text-[10.5px] text-ink-muted">
                        {binding.id}
                        {binding.matches_project_path ? " · matches path" : ""}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function DirectionRow({
  binding,
  plug,
  saving,
  onActivatePlanspace,
  onTogglePlanspaceVisibility,
}: {
  binding: ContextSpaceBindingSummary;
  plug: ContextSpacePlugSummary;
  saving: boolean;
  onActivatePlanspace: (binding_id: string, planspace_id: string) => void;
  onTogglePlanspaceVisibility: (planspaceId: string, hidden: boolean) => void;
}) {
  const hidden = !!plug.hidden;
  return (
    <div
      className={
        "flex items-center gap-2 rounded-md border px-3 py-2 text-[12px] transition " +
        (plug.active
          ? "border-brand bg-brand-soft text-brand-ink"
          : "border-line bg-surface-raised text-ink")
      }
    >
      <button
        type="button"
        disabled={saving}
        onClick={() => onActivatePlanspace(binding.id, plug.id)}
        className="flex min-w-0 flex-1 items-center gap-2 text-left disabled:opacity-50"
      >
        <span
          className="h-2.5 w-2.5 flex-none rounded-full border border-line"
          style={{ background: colorSwatch(plug) }}
          aria-hidden="true"
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{plug.title}</span>
          <span className="mt-0.5 block truncate font-mono text-[10.5px] text-ink-muted">
            {plug.slug}
            {plug.mode ? ` · ${plug.mode}` : ""}
            {plug.active ? " · active" : ""}
            {hidden ? " · hidden" : ""}
          </span>
        </span>
      </button>
      <button
        type="button"
        disabled={saving}
        onClick={() => onTogglePlanspaceVisibility(plug.id, !hidden)}
        className="flex-none rounded border border-line bg-surface px-2 py-1 text-[11px] text-ink-muted transition hover:border-line-strong hover:text-ink disabled:opacity-40"
      >
        {hidden ? "Show" : "Hide"}
      </button>
    </div>
  );
}

function collectDirections(binding: ContextSpaceBindingSummary | undefined) {
  if (!binding) return [];
  return binding.plugs
    .filter((plug) => plug.kind === "planspace")
    .map((plug) => ({ binding, plug }));
}

function colorSwatch(plug: ContextSpacePlugSummary): string {
  const colors: Record<string, string> = {
    indigo: "rgb(95 111 149)",
    teal: "rgb(67 132 122)",
    rose: "rgb(166 92 110)",
    olive: "rgb(116 128 76)",
    steel: "rgb(82 125 154)",
    mauve: "rgb(135 99 143)",
  };
  return (plug.color && colors[plug.color]) || "rgb(var(--border-strong))";
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
      {children}
    </div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="contents">
      <dt className="text-ink-subtle">{label}</dt>
      <dd className="truncate font-mono text-ink" title={value}>
        {value}
      </dd>
    </div>
  );
}
