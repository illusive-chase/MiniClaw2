import { useSyncExternalStore } from "react";

type Listener = () => void;

const listeners = new Set<Listener>();
const EMPTY_GROUP: ReadonlySet<string> = new Set();
let snapshot: ReadonlySet<string> = EMPTY_GROUP;

export function subscribeHoverGroup(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getHoverGroupSnapshot(): ReadonlySet<string> {
  return snapshot;
}

export function setHoverGroup(nodeIds: Iterable<string>): void {
  const next = new Set(nodeIds);
  if (sameMembers(snapshot, next)) return;
  snapshot = next.size > 0 ? next : EMPTY_GROUP;
  for (const listener of listeners) listener();
}

export function useNodeInHoverGroup(nodeId: string): boolean {
  const group = useSyncExternalStore(
    subscribeHoverGroup,
    getHoverGroupSnapshot,
    getHoverGroupSnapshot,
  );
  return group.has(nodeId);
}

function sameMembers(left: ReadonlySet<string>, right: ReadonlySet<string>): boolean {
  if (left.size !== right.size) return false;
  for (const value of left) {
    if (!right.has(value)) return false;
  }
  return true;
}
