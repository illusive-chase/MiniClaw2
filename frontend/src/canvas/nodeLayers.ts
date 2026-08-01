import type { RFNode } from "./layout";

/**
 * React Flow nodes are independent stacking contexts. A popover rendered
 * inside a node therefore cannot rise above a later sibling node by changing
 * only the popover's z-index; its owning node must be elevated as well.
 */
export const PENDING_GATE_NODE_Z_INDEX = 1000;

export function decoratePendingGateLayers(
  nodes: RFNode[],
  pendingGateNodeIds: readonly string[],
): RFNode[] {
  if (pendingGateNodeIds.length === 0) return nodes;
  const pendingIds = new Set(pendingGateNodeIds);
  return nodes.map((node) =>
    pendingIds.has(node.id) && node.zIndex !== PENDING_GATE_NODE_Z_INDEX
      ? { ...node, zIndex: PENDING_GATE_NODE_Z_INDEX }
      : node,
  );
}
