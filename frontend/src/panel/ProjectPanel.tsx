import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import { LANGUAGE_OPTIONS } from "../languages";
import type {
  ContextSpaceBindingSummary,
  ContextSpacePlugSummary,
  PlanspaceMode,
  SessionContextSpaceInfo,
  SessionInfo,
} from "../types";

export type ProjectPanelProps = {
  session: SessionInfo | null;
  contextSpace: SessionContextSpaceInfo | null;
  contextSpaceLoading: boolean;
  contextSpaceSaving: boolean;
  contextSpaceError: string | null;
  settingsSaving: boolean;
  settingsError: string | null;
  onActivatePlanspace: (binding_id: string, planspace_id: string) => void;
  onSelectContextBinding: (binding_id: string) => void;
  onPreferredLanguageChange: (preferredLanguage: string | null) => void;
  onNewDirection: (userSeed: string, mode: PlanspaceMode) => void;
  onContextInit: () => void;
  onContextRefresh: () => void;
  onContextCancel: () => void;
  onTogglePlanspaceVisibility: (planspaceId: string, hidden: boolean) => void;
};

/**
 * Side panel when the project root is selected.
 *
 * Project-root actions are concierge-style: creating a direction launches the
 * bootstrap agent node, while CONTEXT.md init/refresh stay out of the timeline.
 */
export function ProjectPanel({
  session,
  contextSpace,
  contextSpaceLoading,
  contextSpaceSaving,
  contextSpaceError,
  settingsSaving,
  settingsError,
  onActivatePlanspace,
  onSelectContextBinding,
  onPreferredLanguageChange,
  onNewDirection,
  onContextInit,
  onContextRefresh,
  onContextCancel,
  onTogglePlanspaceVisibility,
}: ProjectPanelProps) {
  const [composerOpen, setComposerOpen] = useState(false);
  const [seed, setSeed] = useState("");
  const [newDirectionMode, setNewDirectionMode] = useState<PlanspaceMode>("manual");

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
  const busy = contextSpaceSaving || refreshing || settingsSaving;

  if (!session) {
    return (
      <div className="flex h-full items-center justify-center px-4 text-sm text-ink-muted">
        No project selected.
      </div>
    );
  }

  const submitNewDirection = () => {
    const trimmed = seed.trim();
    if (!trimmed || busy) return;
    onNewDirection(trimmed, newDirectionMode);
    setSeed("");
    setNewDirectionMode("manual");
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
      </div>

      <div className="flex-1 overflow-y-auto bg-surface px-4 py-3 text-sm">
        <section className="mb-5">
          <SectionLabel>Settings</SectionLabel>
          <dl className="mt-1 grid grid-cols-[140px_1fr] gap-x-3 gap-y-1.5 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px]">
            <KV label="Provider" value={session.provider ?? "claude"} />
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
              disabled={settingsSaving}
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
          </div>

          {composerOpen && (
            <div className="mt-3 rounded-md border border-line bg-surface-sunken p-3">
              <label className="block text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
                Direction
              </label>
              <textarea
                value={seed}
                onChange={(event) => setSeed(event.target.value)}
                rows={5}
                placeholder="What direction are you taking? A paragraph is fine."
                className="mt-1 w-full resize-none rounded-md border border-line bg-surface px-3 py-2 text-[13px] leading-relaxed text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
              />
              <div className="mt-2 inline-flex rounded-md border border-line bg-surface p-0.5">
                {(["manual", "auto"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setNewDirectionMode(mode)}
                    className={
                      "rounded px-2.5 py-1 text-[11px] font-medium transition " +
                      (newDirectionMode === mode
                        ? "bg-surface-raised text-ink-strong shadow-card"
                        : "text-ink-muted hover:text-ink")
                    }
                  >
                    {mode}
                  </button>
                ))}
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
                  disabled={busy || seed.trim().length === 0}
                  onClick={submitNewDirection}
                  className="rounded-md bg-brand px-2.5 py-1 text-[11px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
                >
                  Start
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
