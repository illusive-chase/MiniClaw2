import type { NodeInfo, NodePosition } from "../types";
import { nodeLaneResolver } from "./layout";
import { nodeLayoutOwners } from "./nodeLayoutOwners";

export function hydrateLayoutPositions({
  current,
  incoming,
  incomingChanged,
  pending,
  removed,
}: {
  current: Record<string, NodePosition>;
  incoming: Record<string, NodePosition> | undefined;
  incomingChanged: boolean;
  pending: Record<string, NodePosition>;
  removed: Set<string>;
}): Record<string, NodePosition> {
  const positions = { ...(incomingChanged ? incoming : current), ...pending };
  for (const nodeId of removed) {
    delete positions[nodeId];
    if (incomingChanged && !incoming?.[nodeId]) removed.delete(nodeId);
  }
  return positions;
}

export function filterNodePositions(
  nodes: readonly NodeInfo[],
  positions: Readonly<Record<string, NodePosition>> = {},
): Record<string, NodePosition> {
  const resolveLane = nodeLaneResolver(nodes);
  const result: Record<string, NodePosition> = {};
  for (const [nodeId, node] of nodeLayoutOwners(nodes)) {
    const position = positions[nodeId];
    const lane = resolveLane(node);
    const space = lane ? `planspace:${lane}` : "canvas";
    if (position && position.space === space && Number.isFinite(position.x) && Number.isFinite(position.y)) {
      result[nodeId] = { ...position };
    }
  }
  return result;
}

export function nodePositionUpdate(
  nodes: readonly NodeInfo[],
  nodeId: string,
  position: { x: number; y: number },
  canMutate: (nodeId: string) => boolean,
): NodePosition | null {
  const node = nodeLayoutOwners(nodes).get(nodeId);
  if (!node || !canMutate(node.id) || !Number.isFinite(position.x) || !Number.isFinite(position.y)) return null;
  const lane = nodeLaneResolver(nodes)(node);
  return { ...position, space: lane ? `planspace:${lane}` : "canvas" };
}
