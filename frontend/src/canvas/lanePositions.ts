import type { NodePosition } from "../types";

export function isLanePositionId(nodeId: string): boolean {
  return /^planspace:.+$/.test(nodeId);
}

export function lanePositionUpdate(
  nodeId: string,
  position: { x: number; y: number },
  canMutate: boolean,
): NodePosition | null {
  if (!canMutate || !isLanePositionId(nodeId) || !Number.isFinite(position.x) || !Number.isFinite(position.y)) return null;
  return { x: position.x, y: position.y, space: "canvas" };
}

export function filterLanePositions(
  positions: Readonly<Record<string, NodePosition>> = {},
): Record<string, NodePosition> {
  return Object.fromEntries(Object.entries(positions).filter(([nodeId, position]) =>
    position.space === "canvas" && lanePositionUpdate(nodeId, position, true) !== null,
  ));
}
