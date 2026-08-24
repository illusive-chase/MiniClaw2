import type { Edge } from "reactflow";
import type { RFEdge } from "./layout";

/** The withdraw affordance the canvas attaches to one clicked dependency edge. */
export type EdgeDisconnectDecoration = {
  edgeId: string;
  confirming: boolean;
  onRequest: () => void;
  onConfirm: () => void;
  onCancel: () => void;
};

/** Marks the edges that may claim a pointer hit. See the matching rule in
 * `index.css`: every other edge stays `pointer-events: none`. */
export const INTERACTIVE_EDGE_CLASS = "edge-interactive";

/** Which dependency edges are actionable right now.
 *
 * An edge earns its hit ribbon only when clicking it would do something: one of
 * its endpoints is selected, and the dependency is one `canWithdraw` accepts —
 * which for a runtime graph means the target is still an editable virtual. Every
 * other arrow is decoration, and giving decoration a hit area lays dead strips
 * for right-drag panning across the canvas.
 *
 * `keepEdgeId` holds the ribbon on an edge already showing its withdraw
 * control, so the affordance cannot be stranded by a selection change. */
export function resolveInteractiveDependencyEdges(args: {
  edges: RFEdge[];
  selectedRenderIds: ReadonlySet<string>;
  resolveConnectableNodeId: (
    renderId: string,
    role: "source" | "target",
  ) => string | null;
  canWithdraw: (sourceId: string, targetId: string) => boolean;
  keepEdgeId?: string | null;
}): Set<string> {
  const {
    edges,
    selectedRenderIds,
    resolveConnectableNodeId,
    canWithdraw,
    keepEdgeId,
  } = args;
  const interactive = new Set<string>();
  for (const edge of edges) {
    if (edge.id === keepEdgeId) {
      interactive.add(edge.id);
      continue;
    }
    if (edge.type !== "dependency") continue;
    if (
      !selectedRenderIds.has(edge.source) &&
      !selectedRenderIds.has(edge.target)
    ) {
      continue;
    }
    const sourceId = resolveConnectableNodeId(edge.source, "source");
    const targetId = resolveConnectableNodeId(edge.target, "target");
    if (!sourceId || !targetId) continue;
    if (!canWithdraw(sourceId, targetId)) continue;
    interactive.add(edge.id);
  }
  return interactive;
}

export function decorateEdges(
  edges: RFEdge[],
  selectedNodeId: string | null,
  hoverGroup: readonly string[],
  disconnect?: EdgeDisconnectDecoration | null,
  interactiveEdgeIds?: ReadonlySet<string>,
): Edge[] {
  const hoveredNodeIds = new Set(hoverGroup);
  return edges.map((edge) => {
    /* Always restated, never merged: this pass re-decorates edges it may have
     * decorated before, so a stale class would outlive the selection. */
    const className = interactiveEdgeIds?.has(edge.id)
      ? INTERACTIVE_EDGE_CLASS
      : undefined;
    if (
      edge.type === "loads" ||
      edge.type === "produces" ||
      edge.type === "commitLink"
    ) {
      const endpoint =
        edge.source === selectedNodeId ||
        edge.target === selectedNodeId ||
        hoveredNodeIds.has(edge.source) ||
        hoveredNodeIds.has(edge.target);
      return {
        ...edge,
        className,
        style: { ...(edge.style ?? {}), opacity: endpoint ? 0.75 : 0 },
      };
    }
    if (disconnect && edge.id === disconnect.edgeId) {
      const { edgeId: _edgeId, ...affordance } = disconnect;
      return {
        ...edge,
        className,
        selected: true,
        data: { ...(edge.data ?? {}), disconnect: affordance },
      };
    }
    if (
      selectedNodeId &&
      (edge.source === selectedNodeId || edge.target === selectedNodeId)
    ) {
      return { ...edge, className, selected: true };
    }
    return edge.className === className ? edge : { ...edge, className };
  });
}

export function resolveHoverGroup(
  nodeId: string,
  epochMembersByCommitSha: Record<string, string[]>,
  commitHubIdByNodeId: Record<string, string>,
): string[] {
  if (nodeId.startsWith("commit:") && nodeId !== "commit:ghost") {
    const sha = nodeId.slice("commit:".length);
    return [nodeId, ...(epochMembersByCommitSha[sha] ?? [])];
  }
  const commitHubId = commitHubIdByNodeId[nodeId];
  return commitHubId ? [nodeId, commitHubId] : [nodeId];
}
