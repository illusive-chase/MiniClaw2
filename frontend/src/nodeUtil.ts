import type { NodeCategory, NodeInfo } from "./types";

export function canResumeNode(node: NodeInfo): boolean {
  return (
    Boolean(node.provider_session_id) &&
    (node.state === "done" || node.state === "error" || node.state === "cancelled")
  );
}

/** Prefer the latest durable node snapshot across HTTP and WebSocket paths.
 * Missing revisions keep compatibility with servers predating node revisions. */
export function preferNewerNode(current: NodeInfo, incoming: NodeInfo): NodeInfo {
  return (incoming.rev ?? 0) >= (current.rev ?? 0) ? incoming : current;
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

export function shouldOpenCreatedPlanspace(activated: boolean): boolean {
  return activated;
}

/** Lane nodes ordered from most to least recently active. Canvas placement
 * consumes the full list because the newest durable node is not necessarily
 * rendered: completed op nodes are omitted and collapsed template members are
 * represented by their instance box. */
export function nodeIdsByRecentActivityInLane(
  nodes: readonly NodeInfo[],
  planspaceId: string,
): string[] {
  const activityOf = (node: NodeInfo): number =>
    Math.max(
      node.finished_at ?? 0,
      node.started_at ?? 0,
      node.created_at,
    );
  return nodes
    .filter((node) => node.planspace_id === planspaceId)
    .sort((left, right) => {
      const activityDelta = activityOf(right) - activityOf(left);
      if (activityDelta !== 0) return activityDelta;
      return right.created_at - left.created_at;
    })
    .map((node) => node.id);
}

/** The lane's most recently active durable node, independent of rendering. */
export function lastActiveNodeInLane(
  nodes: readonly NodeInfo[],
  planspaceId: string,
): NodeInfo | null {
  const id = nodeIdsByRecentActivityInLane(nodes, planspaceId)[0];
  return id ? nodes.find((node) => node.id === id) ?? null : null;
}

/** The four mutually exclusive kinds a virtual node can be classified as.
 * `library` is carried by `agent_op_kind` rather than `category`, so every
 * reader must consult both fields; this helper is the single place that does.
 * Historical `principle_edit` nodes read as `library` too — they are the same
 * authoring operation under an older name. */
export type NodeClassification = "work" | "planning" | "review" | "library";

export function isLibraryOpKind(opKind?: string | null): boolean {
  return opKind === "library_edit" || opKind === "principle_edit";
}

export function nodeClassification(
  node: Pick<NodeInfo, "category" | "agent_op_kind">,
): NodeClassification {
  if (isLibraryOpKind(node.agent_op_kind)) return "library";
  if (node.category === "planning") return "planning";
  if (node.category === "review") return "review";
  return "work";
}

/** The wire `category` a classification maps onto. `library` and `work` share
 * `regular`; the librarian is distinguished by `agent_op_kind`, not category. */
export function categoryForClassification(
  classification: NodeClassification,
): NodeCategory {
  switch (classification) {
    case "planning":
      return "planning";
    case "review":
      return "review";
    default:
      return "regular";
  }
}

/** Short label for the canvas tile chip. */
export function nodeClassificationChipLabel(node: NodeInfo): string {
  if (isLibraryOpKind(node.agent_op_kind)) return "library";
  if (node.kind === "verifier") return "verify";
  switch (nodeClassification(node)) {
    case "planning":
      return "plan";
    case "review":
      return node.subtype === "human_interact_review"
        ? "human"
        : node.subtype === "code_review"
          ? "code"
          : "review";
    default:
      return "work";
  }
}

/** Long label for panel headers and tooltips. */
export function nodeClassificationLabel(node: NodeInfo): string {
  if (isLibraryOpKind(node.agent_op_kind)) return "librarian";
  if (node.kind === "verifier") return "programmatic";
  switch (nodeClassification(node)) {
    case "planning":
      return "planning";
    case "review":
      return node.subtype === "human_interact_review"
        ? "human review"
        : node.subtype === "code_review"
          ? "code review"
          : "review";
    default:
      return "regular";
  }
}

/** Tailwind classes for the classification chip, matched to the label. */
export function nodeClassificationTone(node: NodeInfo): string {
  switch (nodeClassification(node)) {
    case "library":
      return "border-state-library/30 bg-state-library-soft text-state-library";
    case "planning":
      return "border-brand/30 bg-brand-soft text-brand-ink";
    case "review":
      return "border-state-review/30 bg-state-review-soft text-state-review";
    default:
      return "border-line bg-surface text-ink-muted";
  }
}
