import type { NodePosition } from "../types";

export function isGitPositionId(nodeId: string): boolean {
  return /^commit:([0-9a-f]{7,64}|ghost)$/.test(nodeId);
}

export function gitPositionUpdate(
  nodeId: string,
  position: { x: number; y: number },
  canMutate: boolean,
): NodePosition | null {
  if (!canMutate || !isGitPositionId(nodeId) || !Number.isFinite(position.x) || !Number.isFinite(position.y)) return null;
  return { x: position.x, y: position.y, space: "canvas" };
}

export function filterGitPositions(
  positions: Readonly<Record<string, NodePosition>> = {},
): Record<string, NodePosition> {
  return Object.fromEntries(Object.entries(positions).filter(([nodeId, position]) =>
    position.space === "canvas" && gitPositionUpdate(nodeId, position, true) !== null,
  ));
}
