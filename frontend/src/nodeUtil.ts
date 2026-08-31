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

/** Whether a node's record lives in this host's store partition.
 *
 * An absent `owner_host_id` is not evidence of foreign ownership — it means the
 * server did not report one — so it counts as local rather than locking the
 * node out. Provenance (which device created the project) is deliberately not
 * consulted: it is a synced value and must never decide local authority.
 */
export function nodeBelongsToHost(
  node: Pick<NodeInfo, "owner_host_id">,
  localMachineId: string | undefined,
): boolean {
  const owner = node.owner_host_id;
  return !owner || owner === localMachineId;
}

/** Why this device cannot rewrite a node, or null when it can. */
export type NodeMutationLock =
  | "project_unbound"
  | "store_read_only"
  | "foreign_host"
  | null;

export function nodeMutationLock(
  node: Pick<NodeInfo, "owner_host_id">,
  session: { bound_here: boolean; read_only: boolean; local_machine_id: string },
): NodeMutationLock {
  if (!nodeBelongsToHost(node, session.local_machine_id)) return "foreign_host";
  if (!session.read_only) return null;
  return session.bound_here ? "store_read_only" : "project_unbound";
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

/** The five mutually exclusive kinds a virtual node can be classified as.
 * `library` and `cold` are carried by `agent_op_kind` rather than `category`,
 * so every reader must consult both fields; this helper is the single place
 * that does. Historical `principle_edit` nodes read as `library` too — they are
 * the same authoring operation under an older name. */
export type NodeClassification =
  | "work"
  | "planning"
  | "review"
  | "library"
  | "cold";

export function isLibraryOpKind(opKind?: string | null): boolean {
  return opKind === "library_edit" || opKind === "principle_edit";
}

export function isColdStartOpKind(opKind?: string | null): boolean {
  return opKind === "cold_start";
}

export function nodeClassification(
  node: Pick<NodeInfo, "category" | "agent_op_kind">,
): NodeClassification {
  if (isLibraryOpKind(node.agent_op_kind)) return "library";
  if (isColdStartOpKind(node.agent_op_kind)) return "cold";
  if (node.category === "planning") return "planning";
  if (node.category === "review") return "review";
  return "work";
}

/** The wire `category` a classification maps onto. `library`, `cold` and `work`
 * all share `regular`; those two are distinguished by `agent_op_kind`. */
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

/** The `agent_op_kind` a classification maps onto, or `null` for the kinds that
 * carry none. Cold start and library are the only two that use this axis. */
export function opKindForClassification(
  classification: NodeClassification,
): string | null {
  switch (classification) {
    case "library":
      return "library_edit";
    case "cold":
      return "cold_start";
    default:
      return null;
  }
}

/** Artifact intent is only available on work and planning nodes. Review nodes
 * have their own deliverable contract (the brief plus the handoff text); a
 * library node's deliverable is one library entry, not an artifact. A cold
 * start is told nothing at all, the artifact contract included — it may still
 * write to the outputs directory, and the framework publishes what it finds.
 * Q/A mode follows the same boundary minus the library exclusion — the
 * librarian may still need to ask which entry the user meant. */
export function artifactModeAvailable(
  classification: NodeClassification,
): boolean {
  return classification === "work" || classification === "planning";
}

export function qaModeAvailable(
  classification: NodeClassification,
): boolean {
  return classification !== "review" && classification !== "cold";
}

/** Dependencies and extra principles both reach the agent as injected prompt
 * text, which is exactly what a cold start excludes. Skills stay available:
 * mounting one supplies a capability without telling the model about it. */
export function scheduledDepsAvailable(
  classification: NodeClassification,
): boolean {
  return classification !== "cold";
}

export function extraPrinciplesAvailable(
  classification: NodeClassification,
): boolean {
  return classification !== "cold";
}

/** Short label for the canvas tile chip. */
export function nodeClassificationChipLabel(node: NodeInfo): string {
  if (isLibraryOpKind(node.agent_op_kind)) return "library";
  if (isColdStartOpKind(node.agent_op_kind)) return "cold";
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
  if (isColdStartOpKind(node.agent_op_kind)) return "cold start";
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
    case "cold":
      return "border-line-strong bg-surface-sunken text-ink-muted";
    case "planning":
      return "border-brand/30 bg-brand-soft text-brand-ink";
    case "review":
      return "border-state-review/30 bg-state-review-soft text-state-review";
    default:
      return "border-line bg-surface text-ink-muted";
  }
}
