import type { CanvasSelection } from "../canvas/Canvas";
import type {
  ClientMessage,
  ContextBundle,
  EventRecord,
  InteractionRequest,
  NodeDiff,
  NodeInfo,
  PlanspaceMode,
  SessionContextSpaceInfo,
  SessionInfo,
} from "../types";
import { AgentPanel } from "./AgentPanel";
import { ContextNodePanel } from "./ContextNodePanel";
import { OpPanel } from "./OpPanel";
import { PlanspaceFilePanel } from "./PlanspaceFilePanel";
import { ProjectPanel } from "./ProjectPanel";

type ResolveGatePayload = Omit<
  Extract<ClientMessage, { type: "interaction_response" }>,
  "type" | "id"
>;

export type SidePanelProps = {
  selection: CanvasSelection;
  nodes: NodeInfo[];
  session: SessionInfo | null;

  /* selected-node data */
  events: EventRecord[];
  eventsLoading: boolean;
  diff: NodeDiff | null;
  diffLoading: boolean;
  contextBundle: ContextBundle | null;
  contextBundleLoading: boolean;

  /* context-node data: bundles aggregated across nodes */
  contextBundlesByNodeId: Record<string, ContextBundle | null | undefined>;

  /* project / context-space data */
  contextSpace: SessionContextSpaceInfo | null;
  contextSpaceLoading: boolean;
  contextSpaceSaving: boolean;
  contextSpaceError: string | null;
  settingsSaving: boolean;
  settingsError: string | null;

  /* gates */
  pendingGate: InteractionRequest | null;
  pendingReview: InteractionRequest | null;

  /* callbacks */
  onResolveGate?: (id: string, payload: ResolveGatePayload) => void;
  onResolveReview: (payload: {
    id: string;
    judgment: string;
  }) => void;
  onSelectNode: (nodeId: string) => void;
  onSpawnPhantomFromNode: (nodeId: string) => void;
  onPreferredLanguageChange: (preferredLanguage: string | null) => void;
  onActivatePlanspace: (binding_id: string, planspace_id: string) => void;
  onSelectContextBinding: (binding_id: string) => void;
  onNewDirection: (userSeed: string, mode: PlanspaceMode) => void;
  onPromoteVirtual: (nodeId: string) => void;
  onPlanspaceModeChange: (planspaceId: string, mode: PlanspaceMode) => void;
  onContextInit: () => void;
  onContextRefresh: () => void;
  onContextCancel: () => void;
  onTogglePlanspaceVisibility: (planspaceId: string, hidden: boolean) => void;

  /* Bumped each time a CONTEXT.md refresh completes, so PlanspaceFilePanel
     reloads the file content. */
  contextReloadVersion?: number;
};

/**
 * Polymorphic side panel. The selected node's `kind` decides the shape.
 *
 * Per PRD §2.3, this collapses the legacy six-tab NodeDetail; selection drives
 * the panel's structure rather than a tab label.
 */
export function SidePanel(props: SidePanelProps) {
  const nodesById = new Map(props.nodes.map((n) => [n.id, n]));

  return (
    <aside className="flex w-[650px] flex-none flex-col border-l border-line bg-surface-sunken">
      <Inner {...props} nodesById={nodesById} />
    </aside>
  );
}

function Inner(props: SidePanelProps & { nodesById: Map<string, NodeInfo> }) {
  const {
    selection,
    session,
    events,
    eventsLoading,
    diff,
    diffLoading,
    contextBundle,
    contextBundleLoading,
    contextBundlesByNodeId,
    contextSpace,
    contextSpaceLoading,
    contextSpaceSaving,
    contextSpaceError,
    settingsSaving,
    settingsError,
    pendingGate,
    pendingReview,
    onResolveGate,
    onResolveReview,
    onSelectNode,
    onSpawnPhantomFromNode,
    onPreferredLanguageChange,
    onActivatePlanspace,
    onSelectContextBinding,
    onNewDirection,
    onPromoteVirtual,
    onPlanspaceModeChange,
    onContextInit,
    onContextRefresh,
    onContextCancel,
    onTogglePlanspaceVisibility,
    contextReloadVersion,
    nodesById,
  } = props;

  if (selection.kind === "none") {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-ink-muted">
        Select a node on the canvas to inspect it.
      </div>
    );
  }

  if (selection.kind === "projectRoot") {
    return (
      <ProjectPanel
        session={session}
        contextSpace={contextSpace}
        contextSpaceLoading={contextSpaceLoading}
        contextSpaceSaving={contextSpaceSaving}
        contextSpaceError={contextSpaceError}
        settingsSaving={settingsSaving}
        settingsError={settingsError}
        onActivatePlanspace={onActivatePlanspace}
        onSelectContextBinding={onSelectContextBinding}
        onPreferredLanguageChange={onPreferredLanguageChange}
        onNewDirection={onNewDirection}
        onContextInit={onContextInit}
        onContextRefresh={onContextRefresh}
        onContextCancel={onContextCancel}
        onTogglePlanspaceVisibility={onTogglePlanspaceVisibility}
      />
    );
  }

  if (selection.kind === "agent") {
    const node = nodesById.get(selection.nodeId);
    if (!node) return <Missing />;
    if (!session) return <Missing />;
    return (
      <AgentPanel
        sessionId={session.id}
        node={node}
        nodesById={nodesById}
        events={events}
        eventsLoading={eventsLoading}
        contextBundle={contextBundle}
        contextBundleLoading={contextBundleLoading}
        pendingGate={pendingGate}
        pendingReview={pendingReview}
        onResolveGate={onResolveGate}
        onResolveReview={onResolveReview}
        onSpawnPhantomFromNode={onSpawnPhantomFromNode}
        onPromoteVirtual={onPromoteVirtual}
      />
    );
  }

  if (selection.kind === "op") {
    const node = nodesById.get(selection.nodeId);
    if (!node) return <Missing />;
    return <OpPanel node={node} diff={diff} diffLoading={diffLoading} />;
  }

  if (selection.kind === "planspace") {
    return (
      <PlanspaceLanePanel
        planspaceId={selection.planspaceId}
        contextSpace={contextSpace}
        saving={contextSpaceSaving}
        onModeChange={onPlanspaceModeChange}
      />
    );
  }

  if (selection.kind === "context") {
    /* Find all nodes whose bundle includes this path. */
    const loadedByNodeIds: string[] = [];
    let sample: ContextBundle | null = null;
    for (const [ownerId, bundle] of Object.entries(contextBundlesByNodeId)) {
      if (!bundle) continue;
      if (bundle.sources.some((s) => s.path === selection.path)) {
        loadedByNodeIds.push(ownerId);
        if (!sample) sample = bundle;
      }
    }
    if (session && isPlanspaceFileSelection(selection)) {
      return (
        <PlanspaceFilePanel
          sessionId={session.id}
          loadedByNodeIds={loadedByNodeIds}
          nodesById={nodesById}
          onSelectConsumer={onSelectNode}
          reloadVersion={contextReloadVersion}
        />
      );
    }
    return (
      <ContextNodePanel
        identityKey={selection.identityKey}
        path={selection.path}
        loadedByNodeIds={loadedByNodeIds}
        nodesById={nodesById}
        sampleBundle={sample}
        onSelectConsumer={onSelectNode}
      />
    );
  }

  return <Missing />;
}

function Missing() {
  return (
    <div className="flex h-full items-center justify-center px-6 text-center text-sm text-ink-muted">
      Node no longer exists.
    </div>
  );
}

function PlanspaceLanePanel({
  planspaceId,
  contextSpace,
  saving,
  onModeChange,
}: {
  planspaceId: string;
  contextSpace: SessionContextSpaceInfo | null;
  saving: boolean;
  onModeChange: (planspaceId: string, mode: PlanspaceMode) => void;
}) {
  const plug = contextSpace?.bindings
    .flatMap((binding) => binding.plugs)
    .find((candidate) => candidate.id === planspaceId);
  const mode = plug?.mode ?? "manual";
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-line bg-surface-raised px-4 py-3">
        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          Direction
        </div>
        <h2 className="mt-1 truncate font-display text-[15px] font-semibold leading-snug text-ink-strong">
          {plug?.title || planspaceId}
        </h2>
        <div className="mt-1 font-mono text-[10.5px] text-ink-muted">
          {planspaceId}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto bg-surface px-4 py-3 text-sm">
        <section className="mb-5">
          <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
            Promotion mode
          </div>
          <div className="mt-2 inline-flex rounded-md border border-line bg-surface-sunken p-0.5">
            {(["manual", "auto"] as const).map((option) => (
              <button
                key={option}
                type="button"
                disabled={saving || mode === option}
                onClick={() => onModeChange(planspaceId, option)}
                className={
                  "rounded px-3 py-1.5 text-[12px] font-medium transition disabled:cursor-default " +
                  (mode === option
                    ? "bg-surface-raised text-ink-strong shadow-card"
                    : "text-ink-muted hover:text-ink")
                }
              >
                {option}
              </button>
            ))}
          </div>
        </section>

        <dl className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px]">
          <div className="grid grid-cols-[120px_1fr] gap-3">
            <dt className="text-ink-subtle">Current mode</dt>
            <dd className="font-mono text-ink">{mode}</dd>
          </div>
        </dl>
      </div>
    </div>
  );
}

function isPlanspaceFileSelection(
  selection: Extract<CanvasSelection, { kind: "context" }>,
): boolean {
  return selection.scope === "project-root";
}
