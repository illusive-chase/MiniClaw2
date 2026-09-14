import type { NodeInfo, NodePosition } from "../types";
import { nodeLaneResolver } from "./layout";

export function filterNodePositions(
  nodes: readonly NodeInfo[],
  positions: Readonly<Record<string, NodePosition>> = {},
): Record<string, NodePosition> {
  const resolveLane = nodeLaneResolver(nodes);
  const result: Record<string, NodePosition> = {};
  for (const node of nodes) {
    const position = positions[node.id];
    const lane = resolveLane(node);
    const space = lane ? `planspace:${lane}` : "canvas";
    if (position && position.space === space && Number.isFinite(position.x) && Number.isFinite(position.y)) {
      result[node.id] = { ...position };
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
  const node = nodes.find((candidate) => candidate.id === nodeId);
  if (!node || !canMutate(nodeId) || !Number.isFinite(position.x) || !Number.isFinite(position.y)) return null;
  const lane = nodeLaneResolver(nodes)(node);
  return { ...position, space: lane ? `planspace:${lane}` : "canvas" };
}
