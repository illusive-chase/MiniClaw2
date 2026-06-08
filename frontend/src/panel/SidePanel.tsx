import type { CanvasSelection } from "../canvas/Canvas";
import type {
  ClientMessage,
  ContextBundle,
  EventRecord,
  InteractionRequest,
  NodeDiff,
  NodeInfo,
  SessionContextSpaceInfo,
  SessionInfo,
} from "../types";
import { AgentPanel } from "./AgentPanel";
import { ContextNodePanel } from "./ContextNodePanel";
import { GatePanel } from "./GatePanel";
import { OpPanel } from "./OpPanel";
import { PlanspaceFilePanel } from "./PlanspaceFilePanel";
import { PlanspacePanel } from "./PlanspacePanel";
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
  onActivatePlanspace: (binding_id: string, planspace_id: string) => void;
  onSelectContextBinding: (binding_id: string) => void;
  onNewDirection: (userSeed: string, needsReview: boolean) => void;
  onContextInit: () => void;
  onContextRefresh: () => void;
  onTogglePlanspaceVisibility: (planspaceId: string, hidden: boolean) => void;
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
    <aside className="flex w-[520px] flex-none flex-col border-l border-line bg-surface-sunken">
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
    pendingGate,
    pendingReview,
    onResolveGate,
    onResolveReview,
    onSelectNode,
    onSpawnPhantomFromNode,
    onActivatePlanspace,
    onSelectContextBinding,
    onNewDirection,
    onContextInit,
    onContextRefresh,
    onTogglePlanspaceVisibility,
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
        onActivatePlanspace={onActivatePlanspace}
        onSelectContextBinding={onSelectContextBinding}
        onNewDirection={onNewDirection}
        onContextInit={onContextInit}
        onContextRefresh={onContextRefresh}
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
        events={events}
        eventsLoading={eventsLoading}
        contextBundle={contextBundle}
        contextBundleLoading={contextBundleLoading}
        pendingGate={pendingGate}
        onResolveGate={onResolveGate}
        onSpawnPhantomFromNode={onSpawnPhantomFromNode}
      />
    );
  }

  if (selection.kind === "gate") {
    const node = nodesById.get(selection.nodeId);
    if (!node) return <Missing />;
    return (
      <GatePanel
        node={node}
        pending={pendingReview}
        onSubmit={onResolveReview}
      />
    );
  }

  if (selection.kind === "op") {
    const node = nodesById.get(selection.nodeId);
    if (!node) return <Missing />;
    return <OpPanel node={node} diff={diff} diffLoading={diffLoading} />;
  }

  if (selection.kind === "planspace") {
    if (!session) return <Missing />;
    return (
      <PlanspacePanel
        sessionId={session.id}
        planspaceId={selection.planspaceId}
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
      const role =
        selection.scope === "project-root"
          ? "context"
          : selection.sourceKind === "plan"
            ? "plan"
            : "status";
      return (
        <PlanspaceFilePanel
          sessionId={session.id}
          role={role}
          planspaceId={selection.plugId ?? null}
          loadedByNodeIds={loadedByNodeIds}
          nodesById={nodesById}
          onSelectConsumer={onSelectNode}
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

function isPlanspaceFileSelection(
  selection: Extract<CanvasSelection, { kind: "context" }>,
): boolean {
  if (selection.scope === "project-root") return true;
  return (
    !!selection.plugId &&
    selection.plugId.startsWith("planspaces.") &&
    (selection.sourceKind === "status" || selection.sourceKind === "plan")
  );
}
