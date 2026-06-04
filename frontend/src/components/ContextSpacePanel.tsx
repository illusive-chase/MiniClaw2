import { useEffect, useState } from "react";
import type {
  ContextSpaceBindingSummary,
  ContextSpacePlugSummary,
  SessionContextSpaceInfo,
} from "../types";

type Props = {
  info: SessionContextSpaceInfo | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  onRefresh: () => void;
  onUpdate: (body: {
    project_context_binding_id?: string | null;
    active_planspace_id?: string | null;
  }) => Promise<void>;
  onBootstrap: (body: {
    title?: string;
    planspace_slug?: string;
    binding_slug?: string;
  }) => Promise<void>;
};

export function ContextSpacePanel({
  info,
  loading,
  saving,
  error,
  onRefresh,
  onUpdate,
  onBootstrap,
}: Props) {
  const [bootstrapTitle, setBootstrapTitle] = useState("");
  const resolvedBinding = info
    ? info.bindings.find((binding) => binding.id === info.resolved_binding_id) ?? null
    : null;
  const explicitBindingId = info?.project_context_binding_id ?? "";
  const explicitActivePlanspaceId = info?.project_active_planspace_id ?? "";
  const planspaces = resolvedBinding
    ? resolvedBinding.plugs.filter((plug) => plug.kind === "planspace" && plug.enabled)
    : [];
  const showBootstrap = !!info && (!info.exists || !info.resolved_binding_id);

  useEffect(() => {
    if (info?.resolved_binding_id) {
      setBootstrapTitle("");
    }
  }, [info?.resolved_binding_id]);

  return (
    <div className="flex-1 overflow-y-auto bg-surface px-6 py-5 text-sm text-ink">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
            ContextSpace
          </div>
          <div className="mt-1 truncate font-mono text-xs text-ink-muted">
            {info?.root ?? "loading"}
          </div>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading || saving}
          className="rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink-muted transition hover:border-line-strong hover:bg-surface-sunken hover:text-ink disabled:opacity-40"
        >
          Refresh
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-xs text-state-error">
          {error}
        </div>
      )}

      {loading && !info ? (
        <div className="rounded-md border border-line bg-surface-sunken px-3 py-3 text-xs text-ink-muted">
          Loading ContextSpace...
        </div>
      ) : !info ? (
        <div className="rounded-md border border-line bg-surface-sunken px-3 py-3 text-xs text-ink-muted">
          No ContextSpace data.
        </div>
      ) : (
        <div className="space-y-4">
          {showBootstrap && (
            <section className="rounded-md border border-brand/30 bg-brand-soft/40 p-4">
              <div className="mb-3 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-brand-ink dark:text-brand">
                    Bootstrap
                  </div>
                  <div className="mt-1 font-display text-sm font-semibold text-ink-strong">
                    Create default ContextSpace
                  </div>
                </div>
              </div>
              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
                    Title
                  </span>
                  <input
                    type="text"
                    value={bootstrapTitle}
                    onChange={(event) => setBootstrapTitle(event.target.value)}
                    placeholder="Project planspace"
                    disabled={saving}
                    className="rounded-md border border-line bg-surface px-3 py-2 text-xs text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none disabled:opacity-50"
                  />
                </label>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() =>
                    void onBootstrap({
                      title: bootstrapTitle.trim() || undefined,
                    })
                  }
                  className="self-end rounded-md bg-brand px-3 py-2 text-xs font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-50"
                >
                  {saving ? "Creating..." : "Create"}
                </button>
              </div>
            </section>
          )}

          {info.bootstrap && info.bootstrap.created.length > 0 && (
            <section className="rounded-md border border-state-review/30 bg-state-review-soft px-3 py-2 text-xs text-state-review">
              <div className="font-medium">Created {info.bootstrap.created.length} files</div>
              <div className="mt-1 truncate font-mono" title={info.bootstrap.created.join(", ")}>
                {info.bootstrap.created.join(", ")}
              </div>
            </section>
          )}

          <section className="rounded-md border border-line bg-surface-raised p-4 shadow-card">
            <div className="mb-3 grid grid-cols-2 gap-3 text-xs">
              <ContextMetric label="Root" value={info.exists ? "present" : "missing"} />
              <ContextMetric
                label="Resolved binding"
                value={info.resolved_binding_id ?? "none"}
              />
              <ContextMetric
                label="Active planspace"
                value={info.active_planspace_id ?? "none"}
              />
              <ContextMetric label="Bindings" value={String(info.bindings.length)} />
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <label className="flex flex-col gap-1">
                <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
                  Project binding
                </span>
                <select
                  value={explicitBindingId}
                  disabled={saving}
                  onChange={(event) =>
                    void onUpdate({
                      project_context_binding_id: event.target.value || null,
                      active_planspace_id: null,
                    })
                  }
                  className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs text-ink-strong focus:border-brand focus:outline-none disabled:opacity-50"
                >
                  <option value="">Auto by local path</option>
                  {info.bindings.map((binding) => (
                    <option key={binding.id} value={binding.id}>
                      {binding.title || binding.id}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex flex-col gap-1">
                <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
                  Active planspace
                </span>
                <select
                  value={explicitActivePlanspaceId}
                  disabled={saving || planspaces.length === 0}
                  onChange={(event) =>
                    void onUpdate({
                      active_planspace_id: event.target.value || null,
                    })
                  }
                  className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs text-ink-strong focus:border-brand focus:outline-none disabled:opacity-50"
                >
                  <option value="">Binding default</option>
                  {planspaces.map((plug) => (
                    <option key={plug.id} value={plug.id}>
                      {plug.title || plug.id}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </section>

          <section>
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
                Bindings
              </h3>
              {saving && <span className="text-[10px] text-ink-muted">Saving...</span>}
            </div>
            {info.bindings.length === 0 ? (
              <div className="rounded-md border border-dashed border-line bg-surface-sunken px-3 py-4 text-xs text-ink-muted">
                No project bindings found.
              </div>
            ) : (
              <div className="grid gap-3">
                {info.bindings.map((binding) => (
                  <BindingSummary
                    key={binding.id}
                    binding={binding}
                    selected={binding.id === info.resolved_binding_id}
                  />
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function ContextMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-surface-sunken px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.12em] text-ink-subtle">
        {label}
      </div>
      <div className="mt-1 truncate font-mono text-ink" title={value}>
        {value}
      </div>
    </div>
  );
}

function BindingSummary({
  binding,
  selected,
}: {
  binding: ContextSpaceBindingSummary;
  selected: boolean;
}) {
  const planspaces = binding.plugs.filter((plug) => plug.kind === "planspace");
  const skills = binding.plugs.filter((plug) => plug.kind === "skill");
  const globals = binding.plugs.filter((plug) => plug.kind === "global");
  return (
    <div
      className={
        "rounded-md border bg-surface-raised p-4 shadow-card " +
        (selected ? "border-brand/50 ring-1 ring-brand/20" : "border-line")
      }
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate font-display text-sm font-semibold text-ink-strong">
            {binding.title}
          </div>
          <div className="mt-1 truncate font-mono text-[11px] text-ink-muted">
            {binding.id}
          </div>
        </div>
        <div className="flex flex-none flex-wrap justify-end gap-1 text-[10px]">
          {selected && (
            <span className="rounded border border-brand/30 bg-brand-soft px-1.5 py-0.5 text-brand-ink dark:text-brand">
              selected
            </span>
          )}
          {binding.matches_project_path && (
            <span className="rounded border border-state-review/30 bg-state-review-soft px-1.5 py-0.5 text-state-review">
              path match
            </span>
          )}
        </div>
      </div>

      <dl className="mt-3 grid grid-cols-[130px_1fr] gap-x-3 gap-y-1.5 text-xs">
        <BindingRow label="File" value={binding.path} />
        <BindingRow
          label="Default planspace"
          value={binding.binding_active_planspace_id ?? "(auto)"}
        />
        <BindingRow
          label="Local paths"
          value={binding.local_paths.length ? binding.local_paths.join(", ") : "(none)"}
        />
      </dl>

      <div className="mt-3 grid gap-2 md:grid-cols-3">
        <PlugGroup title="Planspaces" plugs={planspaces} />
        <PlugGroup title="Skills" plugs={skills} />
        <PlugGroup title="Global" plugs={globals} />
      </div>
    </div>
  );
}

function BindingRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-ink-subtle">{label}</dt>
      <dd className="truncate font-mono text-ink" title={value}>
        {value}
      </dd>
    </>
  );
}

function PlugGroup({
  title,
  plugs,
}: {
  title: string;
  plugs: ContextSpacePlugSummary[];
}) {
  return (
    <div className="rounded-md border border-line bg-surface-sunken px-3 py-2">
      <div className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.12em] text-ink-subtle">
        {title}
      </div>
      {plugs.length === 0 ? (
        <div className="text-[11px] text-ink-subtle">none</div>
      ) : (
        <div className="space-y-1.5">
          {plugs.map((plug) => (
            <PlugLine key={`${plug.source}:${plug.id}`} plug={plug} />
          ))}
        </div>
      )}
    </div>
  );
}

function PlugLine({ plug }: { plug: ContextSpacePlugSummary }) {
  return (
    <div className="min-w-0">
      <div className="flex min-w-0 items-center gap-1.5">
        <span
          className={
            "h-1.5 w-1.5 flex-none rounded-full " +
            (plug.active
              ? "bg-brand"
              : plug.enabled && plug.exists
                ? "bg-state-review"
                : "bg-ink-subtle")
          }
        />
        <span className="truncate font-mono text-[11px] text-ink" title={plug.id}>
          {plug.id}
        </span>
      </div>
      <div className="ml-3 mt-0.5 flex flex-wrap gap-x-2 gap-y-1 text-[10px] text-ink-subtle">
        <span>{plug.source}</span>
        {plug.auto_update && <span>auto update</span>}
        {!plug.enabled && <span>disabled</span>}
        {!plug.exists && <span>missing</span>}
      </div>
    </div>
  );
}
