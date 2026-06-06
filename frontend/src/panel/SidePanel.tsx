import type { CanvasSelection } from "../canvas/Canvas";
import type {
  ClientMessage,
  ContextBundle,
  EventRecord,
  InteractionRequest,
  NodeArtifact,
  NodeDiff,
  NodeInfo,
  SessionContextSpaceInfo,
  SessionInfo,
} from "../types";
import { AgentPanel } from "./AgentPanel";
import { ArtifactPanel } from "./ArtifactPanel";
import { ContextNodePanel } from "./ContextNodePanel";
import { GatePanel } from "./GatePanel";
import { OpPanel } from "./OpPanel";
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
  artifact: NodeArtifact | null;
  artifactLoading: boolean;
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
    decision: "write-json" | "no-op";
    path?: string;
    payload?: unknown;
    notes?: string;
  }) => void;
  onSelectNode: (nodeId: string) => void;
  onSpawnPhantomFromNode: (nodeId: string) => void;
  onActivatePlanspace: (binding_id: string, planspace_id: string) => void;
  onBootstrapContextSpace: () => void;
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
    nodes,
    session,
    events,
    eventsLoading,
    artifact,
    artifactLoading,
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
    onBootstrapContextSpace,
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
        onBootstrapContextSpace={onBootstrapContextSpace}
      />
    );
  }

  if (selection.kind === "agent") {
    const node = nodesById.get(selection.nodeId);
    if (!node) return <Missing />;
    return (
      <AgentPanel
        node={node}
        events={events}
        eventsLoading={eventsLoading}
        artifact={artifact}
        artifactLoading={artifactLoading}
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
    /* Find upstream review_brief artifact (the producer of this gate) */
    const briefOwner = node.parent_node_id ? nodesById.get(node.parent_node_id) : null;
    const briefArtifact =
      briefOwner && briefOwner.output_kind === "review_brief" && briefOwner.output_path
        ? { ownerNodeId: briefOwner.id, path: briefOwner.output_path }
        : null;
    return (
      <GatePanel
        node={node}
        pending={pendingReview}
        briefArtifact={briefArtifact}
        onSelectBrief={onSelectNode}
        onSubmit={onResolveReview}
      />
    );
  }

  if (selection.kind === "op") {
    const node = nodesById.get(selection.nodeId);
    if (!node) return <Missing />;
    return <OpPanel node={node} diff={diff} diffLoading={diffLoading} />;
  }

  if (selection.kind === "artifact") {
    const owner = nodesById.get(selection.ownerNodeId) ?? null;
    const consumers = findConsumersOfPath(nodes, selection.path);
    return (
      <ArtifactPanel
        owner={owner}
        artifact={artifact}
        artifactLoading={artifactLoading}
        path={selection.path}
        artifactKind={selection.artifactKind}
        consumers={consumers}
        onSelectOwner={onSelectNode}
        onSelectConsumer={onSelectNode}
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

function findConsumersOfPath(nodes: NodeInfo[], path: string): NodeInfo[] {
  return nodes.filter((n) => n.context_sources?.includes(path));
}
