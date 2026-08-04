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
