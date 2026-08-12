import type { NodeInfo } from "./types";

export function canResumeNode(node: NodeInfo): boolean {
  return (
    Boolean(node.provider_session_id) &&
    (node.state === "done" || node.state === "error" || node.state === "cancelled")
  );
}

export function nodeIdsNeedingEventReplay(
  nodes: readonly Pick<NodeInfo, "id" | "state">[],
): string[] {
  // A queued node may start before the next list refresh, so its socket stream
  // must be replay-ready before the first node_started event is broadcast.
  return nodes
    .filter(
      (node) =>
        node.state === "queued" ||
        node.state === "running" ||
        node.state === "waiting" ||
        node.state === "awaiting_human_input",
    )
    .map((node) => node.id);
}

/** Background runner events may surface a node only while the user has no
 * active canvas selection. Replacing any existing selection can unmount an
 * editor in the details panel before its local draft is saved. */
export function shouldAutoSelectEventNode(selection: { kind: string }): boolean {
  return selection.kind === "none";
}

/** A response form may reclaim focus when it belongs to the execution node
 * already selected. Other active selections stay intact so editors are not
 * replaced by an unrelated background request. */
export function shouldOpenInteractionNode(
  selection: { kind: string; nodeId?: string },
  nodeId: string,
): boolean {
  return (
    selection.kind === "none" ||
    ((selection.kind === "agent" || selection.kind === "op") &&
      selection.nodeId === nodeId)
  );
}
