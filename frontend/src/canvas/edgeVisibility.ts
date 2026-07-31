import type { Edge } from "reactflow";
import type { RFEdge } from "./layout";

export function decorateEdges(
  edges: RFEdge[],
  selectedNodeId: string | null,
  hoverGroup: readonly string[],
): Edge[] {
  const hoveredNodeIds = new Set(hoverGroup);
  return edges.map((edge) => {
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
        style: { ...(edge.style ?? {}), opacity: endpoint ? 0.75 : 0 },
      };
    }
    if (
      selectedNodeId &&
      (edge.source === selectedNodeId || edge.target === selectedNodeId)
    ) {
      return { ...edge, selected: true };
    }
    return edge;
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
