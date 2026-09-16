import type { NodePosition } from "../types";

export function isContextPositionId(nodeId: string): boolean {
  return /^ctx:.+::.+::.+$/.test(nodeId);
}

export function contextPositionUpdate(
  node: { id: string; type?: string; parentNode?: string } | undefined,
  position: { x: number; y: number },
  canMutate: boolean,
): NodePosition | null {
  if (!canMutate || !node || node.type !== "context" || !isContextPositionId(node.id)) return null;
  const space = node.parentNode ?? "canvas";
  if (!/^(canvas|planspace:.+)$/.test(space) || !Number.isFinite(position.x) || !Number.isFinite(position.y)) return null;
  return { x: position.x, y: position.y, space };
}

export function filterContextPositions(
  positions: Readonly<Record<string, NodePosition>> = {},
): Record<string, NodePosition> {
  return Object.fromEntries(Object.entries(positions).filter(([nodeId, position]) =>
    isContextPositionId(nodeId) && /^(canvas|planspace:.+)$/.test(position.space)
      && Number.isFinite(position.x) && Number.isFinite(position.y),
  ));
}
