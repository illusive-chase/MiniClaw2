import type {
  SessionContextSpaceInfo,
  SessionInfo,
} from "../types";

export type ProjectPanelProps = {
  session: SessionInfo | null;
  contextSpace: SessionContextSpaceInfo | null;
  contextSpaceLoading: boolean;
  contextSpaceSaving: boolean;
  contextSpaceError: string | null;
  onActivatePlanspace: (binding_id: string, planspace_id: string) => void;
  onBootstrapContextSpace: () => void;
};

/**
 * Side panel when the project root is selected.
 *
 * Per PRD §5.3: project settings (provider, auto-commit, scenario name) live here,
 * along with planspace-activation (click-to-activate, replacing the dropdowns).
 */
export function ProjectPanel({
  session,
  contextSpace,
  contextSpaceLoading,
  contextSpaceSaving,
  contextSpaceError,
  onActivatePlanspace,
  onBootstrapContextSpace,
}: ProjectPanelProps) {
  if (!session) {
    return (
      <div className="flex h-full items-center justify-center px-4 text-sm text-ink-muted">
        No project selected.
      </div>
    );
  }
  const activeBinding = contextSpace?.bindings.find(
    (b) => b.id === (contextSpace?.resolved_binding_id ?? session.project_context_binding_id),
  );
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
            <KV label="Scenario" value={session.scenario_name ?? "(none)"} />
            <KV label="Turns" value={String(session.turns)} />
            <KV
              label="Created"
              value={new Date(session.created_at * 1000).toLocaleString()}
            />
          </dl>
        </section>

        <section className="mb-5">
          <div className="flex items-baseline justify-between">
            <SectionLabel>Active planspace</SectionLabel>
            {contextSpaceLoading && (
              <span className="text-[10px] text-ink-subtle">loading…</span>
            )}
          </div>
          {contextSpaceError && (
            <div className="mt-1 rounded-md border border-state-error/30 bg-state-error-soft p-2 text-xs text-state-error">
              {contextSpaceError}
            </div>
          )}
          {!contextSpace ? (
            <div className="mt-1 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px] text-ink-muted">
              No context space loaded.
            </div>
          ) : !contextSpace.exists ? (
            <div className="mt-1 space-y-2 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px]">
              <p className="text-ink-muted">
                No context space yet. Create one to enable persistent project memory.
              </p>
              <button
                type="button"
                onClick={onBootstrapContextSpace}
                disabled={contextSpaceSaving}
                className="rounded-md bg-brand px-2.5 py-1 text-[11px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
              >
                Bootstrap context space
              </button>
            </div>
          ) : !activeBinding ? (
            <div className="mt-1 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px] text-ink-muted">
              No binding resolved. Select a context node on the canvas to attach one.
            </div>
          ) : (
            <ul className="mt-1 space-y-1">
              {activeBinding.plugs
                .filter((p) => p.kind === "planspace")
                .map((p) => (
                  <li key={p.id}>
                    <button
                      type="button"
                      disabled={contextSpaceSaving}
                      onClick={() => onActivatePlanspace(activeBinding.id, p.id)}
                      className={
                        "block w-full rounded-md border px-3 py-2 text-left text-[12px] transition disabled:opacity-50 " +
                        (p.active
                          ? "border-brand bg-brand-soft text-brand-ink"
                          : "border-line bg-surface-raised text-ink hover:border-line-strong")
                      }
                    >
                      <div className="line-clamp-1 font-medium">{p.title}</div>
                      <div className="mt-0.5 font-mono text-[10.5px] text-ink-muted">
                        {p.slug}
                        {p.active ? " · active" : ""}
                      </div>
                    </button>
                  </li>
                ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
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
