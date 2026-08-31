/* Rules for declaring and withdrawing a dependency by dragging on the canvas.
 *
 * The canvas and the inspector's checkbox list write the same field through the
 * same endpoint, so these rules mirror the backend's gates rather than adding
 * any of their own — see `registry.update_virtual` (the kind/state gate),
 * `_normalize_virtual_scheduled_deps` (self-reference, lane), and the cycle
 * check that runs after the update materializes. Rejecting here means the user
 * feels an illegal drop refuse to land instead of watching it snap into place
 * and then vanish behind a 400.
 *
 * Kept free of React Flow types so the rules can be tested as functions.
 */

import type { NodeInfo } from "../types";
import { isColdStartOpKind } from "../nodeUtil";

/** Why a dragged connection cannot become a dependency. */
export type ConnectionRejection =
  | "missing-endpoint"
  | "self-reference"
  | "target-not-agent"
  | "target-not-virtual"
  | "target-obsolete"
  | "target-cold-start"
  | "cross-lane"
  | "already-declared"
  | "would-cycle";

export type ConnectionAttempt = {
  sourceId: string | null | undefined;
  targetId: string | null | undefined;
};

/* Mirrors `has_cycle` in backend/miniclaw2/virtual_graph.py — a 3-colour DFS
 * over scheduled_deps, treating unknown ids as resolved leaves. Asks whether
 * the proposed dep is reachable from the node that would own it: if the source
 * already depends on the target, adding target→source closes a loop. */
function dependsOn(
  fromId: string,
  soughtId: string,
  nodesById: Map<string, NodeInfo>,
): boolean {
  const seen = new Set<string>();
  const stack = [fromId];
  while (stack.length) {
    const current = stack.pop() as string;
    if (current === soughtId) return true;
    if (seen.has(current)) continue;
    seen.add(current);
    for (const dep of nodesById.get(current)?.scheduled_deps ?? []) {
      if (!seen.has(dep)) stack.push(dep);
    }
  }
  return false;
}

/** The rejection reason for a dragged connection, or null when it is legal. */
export function dependencyConnectionRejection(
  attempt: ConnectionAttempt,
  nodesById: Map<string, NodeInfo>,
): ConnectionRejection | null {
  const { sourceId, targetId } = attempt;
  if (!sourceId || !targetId) return "missing-endpoint";
  if (sourceId === targetId) return "self-reference";

  const source = nodesById.get(sourceId);
  const target = nodesById.get(targetId);
  if (!source || !target) return "missing-endpoint";

  /* The target owns the array, so only the target must be an editable virtual.
   * An op node may be depended upon; it just cannot hold dependencies. */
  if (target.kind !== "agent") return "target-not-agent";
  if (target.state !== "virtual") return "target-not-virtual";
  if (target.obsolete_reason) return "target-obsolete";
  if (isColdStartOpKind(target.agent_op_kind)) return "target-cold-start";
  if ((source.planspace_id ?? "") !== (target.planspace_id ?? "")) {
    return "cross-lane";
  }
  if ((target.scheduled_deps ?? []).includes(sourceId)) {
    return "already-declared";
  }
  if (dependsOn(sourceId, targetId, nodesById)) return "would-cycle";
  return null;
}

export function canConnectDependency(
  attempt: ConnectionAttempt,
  nodesById: Map<string, NodeInfo>,
): boolean {
  return dependencyConnectionRejection(attempt, nodesById) === null;
}

/** Whether an existing dependency edge may be withdrawn. Same gate as
 * declaring one: the target must still be an editable virtual. */
export function canDisconnectDependency(
  sourceId: string,
  targetId: string,
  nodesById: Map<string, NodeInfo>,
): boolean {
  const target = nodesById.get(targetId);
  if (!target) return false;
  if (target.kind !== "agent") return false;
  if (target.state !== "virtual") return false;
  if (target.obsolete_reason) return false;
  return (target.scheduled_deps ?? []).includes(sourceId);
}

/* The dependencies `targetId` keeps after withdrawing `sourceId`.
 *
 * Only scheduled_deps is touched. The template editor clears a matching
 * `resume_from` alongside a disconnect, but that is its own rule: at runtime
 * the two fields are independent relations drawn as separate edges, and
 * UpdateVirtualPayload has no resume field to write. A resume edge that
 * outlives its dependency edge is correct. */
export function scheduledDepsAfterDisconnect(
  target: NodeInfo,
  sourceId: string,
): string[] {
  return (target.scheduled_deps ?? []).filter((id) => id !== sourceId);
}

export function scheduledDepsAfterConnect(
  target: NodeInfo,
  sourceId: string,
): string[] {
  const current = target.scheduled_deps ?? [];
  return current.includes(sourceId) ? [...current] : [...current, sourceId];
}

export type ScheduledDepsRewrite = (current: string[]) => string[] | null;

export type ScheduledDepsUpdate = {
  getTarget: () => NodeInfo | undefined;
  canMutate: (target: NodeInfo) => boolean;
  write: (scheduledDeps: string[]) => Promise<NodeInfo | undefined>;
};

/** Serializes whole-array dependency rewrites per target. The preceding
 * response is the base for the next rewrite, while unrelated targets remain
 * independent. */
export class ScheduledDepsUpdateQueue {
  private readonly pending = new Map<
    string,
    Promise<NodeInfo | undefined>
  >();

  enqueue(
    targetId: string,
    rewrite: ScheduledDepsRewrite,
    update: ScheduledDepsUpdate,
  ): Promise<NodeInfo | undefined> {
    const previous = this.pending.get(targetId) ?? Promise.resolve(undefined);
    const queued = previous
      .catch(() => undefined)
      .then(async (previousTarget) => {
        const target =
          previousTarget?.id === targetId ? previousTarget : update.getTarget();
        if (!target || !update.canMutate(target)) return target;

        const current = target.scheduled_deps ?? [];
        const next = rewrite(current);
        if (
          next === null ||
          (next.length === current.length &&
            next.every((dependencyId, index) => dependencyId === current[index]))
        ) {
          return target;
        }
        return update.write(next);
      });
    this.pending.set(targetId, queued);

    const clear = () => {
      if (this.pending.get(targetId) === queued) this.pending.delete(targetId);
    };
    void queued.then(clear, clear);
    return queued;
  }
}

/** Id of the dependency edge `buildGraph` emits for one pair, before the
 * collapsed-instance remapping rewrites its endpoints. */
export function dependencyEdgeId(sourceId: string, targetId: string): string {
  return `dep:${sourceId}->${targetId}`;
}

/* What releasing a dragged wire should do.
 *
 * The gesture starts on a node's own dependency button, so the source is always
 * a node the user can see. Where it lands decides the meaning: another node
 * declares a dependency between two existing nodes, empty canvas asks for a new
 * downstream virtual that waits for the source. Those are the same two things
 * the button's plain click and the inspector's checkbox list already do; the
 * drag just lets one gesture pick between them.
 */
export type WiringDropAction =
  | { kind: "connect"; sourceId: string; targetId: string }
  | { kind: "create"; sourceId: string }
  | {
      kind: "none";
      reason: ConnectionRejection | "source-missing" | "blocked-surface";
    };

/** What is under the pointer when a wire is released. Keeping an invalid
 * surface distinct from the canvas pane prevents releases over controls,
 * sidebars, or collapsed instance boxes from creating a node. */
export type WiringDropSurface =
  | { kind: "canvas" }
  | { kind: "target"; targetId: string }
  | { kind: "blocked" };

/**
 * A target id is the durable node the drop landed on, already resolved through
 * the collapsed-instance remapping. Only an explicit canvas surface creates a
 * node; blocked surfaces always cancel the gesture.
 *
 * Releasing back on the source node is deliberately `none` rather than
 * `create`: a long press that the user thought better of and released in place
 * should not leave a node behind.
 */
export function resolveWiringDrop(
  sourceId: string,
  surface: WiringDropSurface,
  nodesById: Map<string, NodeInfo>,
): WiringDropAction {
  if (!nodesById.has(sourceId)) {
    return { kind: "none", reason: "source-missing" };
  }
  if (surface.kind === "blocked") {
    return { kind: "none", reason: "blocked-surface" };
  }
  if (surface.kind === "canvas") return { kind: "create", sourceId };
  const { targetId } = surface;
  if (targetId === sourceId) return { kind: "none", reason: "self-reference" };
  const rejection = dependencyConnectionRejection(
    { sourceId, targetId },
    nodesById,
  );
  return rejection === null
    ? { kind: "connect", sourceId, targetId }
    : { kind: "none", reason: rejection };
}
