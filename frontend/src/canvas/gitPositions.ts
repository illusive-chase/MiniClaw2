import type { CommitDescriptor, NodePosition } from "../types";

export type CommitPositionTarget = { sha: string; position: NodePosition };

let changesPositionResolver: (() => { x: number; y: number } | null) | null = null;

export function setGitChangesPositionResolver(
  resolver: typeof changesPositionResolver,
): void {
  changesPositionResolver = resolver;
}

export function captureGitChangesPosition(): NodePosition | null {
  const position = changesPositionResolver?.();
  return position ? gitPositionUpdate("commit:ghost", position, true) : null;
}

export function resolveCommitPositionTarget(
  commits: readonly CommitDescriptor[],
  target: CommitPositionTarget | null,
  canMutate: boolean,
): CommitPositionTarget | null {
  if (!target || !canMutate) return null;
  const commit = commits.find((candidate) =>
    candidate.sha === target.sha || candidate.aliases?.includes(target.sha),
  );
  if (!commit) return null;
  const position = gitPositionUpdate(`commit:${commit.sha}`, target.position, true);
  return position ? { sha: commit.sha, position } : null;
}

export function transferGitPosition(
  positions: Readonly<Record<string, NodePosition>>,
  target: CommitPositionTarget | null,
): Record<string, NodePosition> {
  const next = { ...positions };
  if (target) {
    delete next["commit:ghost"];
    next[`commit:${target.sha}`] = target.position;
  }
  return next;
}

export function preserveGitRuntimePosition(
  nodeId: string,
  positions: Readonly<Record<string, NodePosition>>,
  target: CommitPositionTarget | null,
): boolean {
  if (target && nodeId === `commit:${target.sha}`) return false;
  return nodeId !== "commit:ghost" || (!target && !!positions[nodeId]);
}

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
