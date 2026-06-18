import type { Edge, Node } from "reactflow";
import type { ContextBundle, NodeInfo } from "../types";

/* ───────── canvas node payloads ───────── */

export type AgentNodeData = {
  node: NodeInfo;
  index: number;
  resumeParent: NodeInfo | null;
  /** true when this agent is currently streaming text in the live channel */
  isActive: boolean;
  planspaceColor: PlanspaceColor | null;
  /** true when no agent node (in any lane) has this one as its agent parent */
  isLastInLane: boolean;
  readyToPromote: boolean;
};

export type OpNodeData = {
  node: NodeInfo;
  parent: NodeInfo | null;
  child: NodeInfo | null;
};

export type ErrorTerminalData = {
  /** The owning agent node whose error this surfaces. */
  ownerNodeId: string;
  message: string;
};

export type ContextNodeData = {
  /** stable identity from path+scope+kind so the node survives reloads */
  identityKey: string;
  scope: string;
  kind: string;
  path: string;
  filename: string;
  chars: number;
  /** ids of agent nodes that loaded this file */
  loadedByNodeIds: string[];
  /** source plug id when this context file comes from a planspace/skill plug */
  plugId?: string | null;
};

export type PhantomNodeData = {
  /** id of the node we're resuming from, if any */
  resumeFromNodeId: string | null;
  /** display label for the parent ("Build calculator", "fresh start") */
  resumeFromLabel: string | null;
  /** true when the composer cannot launch a node */
  disabled: boolean;
};

export type ProjectRootNodeData = {
  title: string;
};

export type PlanspaceColor = {
  name: string;
  bg: string;
  border: string;
  accent: string;
  text: string;
};

export type PlanspaceLaneData = {
  planspaceId: string;
  label: string;
  nodeCount: number;
  width: number;
  height: number;
  color: PlanspaceColor;
  active: boolean;
};

export type RFNodeData =
  | AgentNodeData
  | OpNodeData
  | ContextNodeData
  | PhantomNodeData
  | ProjectRootNodeData
  | PlanspaceLaneData
  | ErrorTerminalData;

export type RFNode = Node<RFNodeData>;
export type RFEdge = Edge;

/* ───────── geometry ───────── */

export const LANE = {
  rootX: 40,
  timelineY: 220,
  projectContextLaneY: 8,
  contextLaneY: 110,
  artifactOffsetX: 240,
  artifactOffsetY: 140,
  agentWidth: 224,
  agentSpacing: 280,
  opWidth: 96,
  opSpacing: 140,
  /* Lane is laid out vertically as: header band → ctx row → agent row → bottom pad.
   * Y values below are RELATIVE positions inside the lane (origin = lane top-left). */
  planspaceLaneSpacing: 360,
  planspaceLanePaddingX: 40,
  planspaceLaneCtxRowY: 52,
  planspaceLaneAgentRowY: 156,
  planspaceLaneHeight: 320,
  /* Horizontal step between ctx tiles inside a lane (tile width ~160 + gap). */
  planspaceCtxStep: 180,
};

export const PLANSPACE_PALETTE: PlanspaceColor[] = [
  {
    name: "indigo",
    bg: "rgb(95 111 149 / 0.08)",
    border: "rgb(95 111 149 / 0.28)",
    accent: "rgb(95 111 149)",
    text: "rgb(70 82 112)",
  },
  {
    name: "teal",
    bg: "rgb(67 132 122 / 0.08)",
    border: "rgb(67 132 122 / 0.28)",
    accent: "rgb(67 132 122)",
    text: "rgb(44 103 95)",
  },
  {
    name: "rose",
    bg: "rgb(166 92 110 / 0.08)",
    border: "rgb(166 92 110 / 0.28)",
    accent: "rgb(166 92 110)",
    text: "rgb(126 67 82)",
  },
  {
    name: "olive",
    bg: "rgb(116 128 76 / 0.08)",
    border: "rgb(116 128 76 / 0.28)",
    accent: "rgb(116 128 76)",
    text: "rgb(83 95 52)",
  },
  {
    name: "steel",
    bg: "rgb(82 125 154 / 0.08)",
    border: "rgb(82 125 154 / 0.28)",
    accent: "rgb(82 125 154)",
    text: "rgb(54 94 123)",
  },
  {
    name: "mauve",
    bg: "rgb(135 99 143 / 0.08)",
    border: "rgb(135 99 143 / 0.28)",
    accent: "rgb(135 99 143)",
    text: "rgb(102 72 110)",
  },
];

/* ───────── build graph ───────── */

export type BuildGraphArgs = {
  nodes: NodeInfo[];
  /** id of the currently-streaming node so we can mark it active */
  activeNodeId: string | null;
  /** project-level title to anchor as the root node */
  projectTitle: string;
  /** id of a focused phantom (composer), if any */
  phantomFromNodeId: string | null | undefined;
  /** if a phantom is open as fresh-start, true */
  phantomFreshStart: boolean;
  /** if the phantom composer should be rendered disabled */
  phantomDisabled: boolean;
  /** per-node manual position overrides (drag persistence — client-side for now) */
  layoutHints: Record<string, { x: number; y: number }>;
  /** per-node context bundles, keyed by node id, used to materialize context + loads edges */
  contextBundlesByNodeId: Record<string, ContextBundle | null | undefined>;
  /** planspaces known from the project binding, including empty lanes */
  knownPlanspaceIds: string[];
  /** planspaces hidden by per-project view state */
  hiddenPlanspaceIds: string[];
  /** active write target */
  activePlanspaceId: string | null;
};

export type BuildGraphResult = {
  rfNodes: RFNode[];
  rfEdges: RFEdge[];
};

/**
 * Build the React Flow node + edge list from the backend NodeInfo[].
 *
 * Layout strategy is *append-don't-reflow*: each node is placed deterministically by
 * its index in the input. Manual drags persisted via `layoutHints` override the
 * default position. This means a new node never shoves an existing one off-screen.
 */
export function buildGraph(args: BuildGraphArgs): BuildGraphResult {
  const {
    nodes,
    activeNodeId,
    projectTitle,
    phantomFromNodeId,
    phantomFreshStart,
    phantomDisabled,
    layoutHints,
    contextBundlesByNodeId,
    knownPlanspaceIds,
    hiddenPlanspaceIds,
    activePlanspaceId,
  } = args;

  const rfNodes: RFNode[] = [];
  const rfEdges: RFEdge[] = [];
  const hiddenPlanspaces = new Set(hiddenPlanspaceIds);
  const allNodeById = new Map<string, NodeInfo>();
  for (const n of nodes) allNodeById.set(n.id, n);
  const visibleNodes = nodes.filter((node) => {
    const planspaceId = resolvePlanspaceId(node, allNodeById);
    return !planspaceId || !hiddenPlanspaces.has(planspaceId);
  });
  const nodeById = new Map<string, NodeInfo>();
  for (const n of visibleNodes) nodeById.set(n.id, n);
  const planspaceOrder = collectPlanspaceOrder(visibleNodes, allNodeById);
  for (const id of knownPlanspaceIds) {
    if (!id || hiddenPlanspaces.has(id) || planspaceOrder.includes(id)) continue;
    planspaceOrder.push(id);
  }
  const planspaceIndex = new Map(planspaceOrder.map((id, index) => [id, index]));

  /* Lane absolute positions: deterministic from index, overridable by a saved
   * hint keyed `planspace:<id>`. Children inside the lane get parent-relative
   * positions, so the lane's own abs position must be resolved BEFORE we lay
   * out the children — otherwise a hinted lane drag would shift everything. */
  const laneAbsPos = new Map<string, { x: number; y: number }>();
  for (const id of planspaceOrder) {
    const idx = planspaceIndex.get(id) ?? 0;
    const defaultPos = {
      x: LANE.rootX + 180 - LANE.planspaceLanePaddingX,
      y: LANE.timelineY + idx * LANE.planspaceLaneSpacing - LANE.planspaceLaneAgentRowY,
    };
    laneAbsPos.set(id, layoutHints[`planspace:${id}`] ?? defaultPos);
  }
  /* Per-lane growable width + node count, harvested during the child pass. */
  const laneChildMaxX = new Map<string, number>();
  const laneChildCount = new Map<string, number>();
  const laneColors = new Map<string, PlanspaceColor>();

  /* project root anchor */
  rfNodes.push({
    id: "root",
    type: "projectRoot",
    position: layoutHints["root"] ?? { x: LANE.rootX, y: LANE.timelineY },
    data: { title: projectTitle },
    draggable: true,
    selectable: true,
  });

  /* Index ops by the child node they sit between, so we can fold them onto
   * a single chevron edge from (op's parent) → (op's child). Trailing ops
   * (no child yet) keep their tile rendering so the auto-commit stays visible. */
  const opByChildId = new Map<string, NodeInfo>();
  const opsWithChild = new Set<string>();
  for (const node of visibleNodes) {
    if (node.kind !== "op") continue;
    const childIdx = visibleNodes.findIndex(
      (n) => n.parent_node_id === node.id && n.kind !== "op",
    );
    if (childIdx >= 0) {
      opByChildId.set(visibleNodes[childIdx].id, node);
      opsWithChild.add(node.id);
    }
  }

  const planspaceColorOverrides = collectPlanspaceColorOverrides(
    contextBundlesByNodeId,
  );

  /* Mark agent nodes that no other agent descends from. The "+" hover
   * affordance only appears on these tail tiles. Ops are transparent here: we
   * walk through them to find the real agent parent. */
  const hasDescendantById = new Set<string>();
  for (const candidate of visibleNodes) {
    if (candidate.kind === "op") continue;
    let parentId: string | null | undefined = candidate.parent_node_id;
    while (parentId) {
      const parent = nodeById.get(parentId);
      if (!parent) {
        parentId = null;
        break;
      }
      if (parent.kind !== "op") break;
      parentId = parent.parent_node_id;
    }
    if (parentId && nodeById.has(parentId)) {
      hasDescendantById.add(parentId);
    }
  }

  /* Main timeline. Two coordinate regimes coexist:
   *   - Nodes WITH a planspace become children of `planspace:<id>` lanes:
   *     position is relative to lane, advanced by a per-lane cursor.
   *   - Nodes WITHOUT a planspace stay top-level in absolute coords,
   *     advanced by `freeCursorX`.
   * `extent: "parent"` keeps in-lane nodes inside their swimlane, giving the
   * group container real semantics instead of mere visuals. */
  let freeCursorX = initialFreeCursorX(
    planspaceOrder,
    laneAbsPos,
    visibleNodes,
    allNodeById,
    opsWithChild,
    layoutHints,
  );
  const laneCursors = new Map<string, number>();
  const advanceLane = (laneId: string, by: number): number => {
    const cur = laneCursors.get(laneId) ?? LANE.planspaceLanePaddingX;
    laneCursors.set(laneId, cur + by);
    return cur;
  };
  const recordChildExtent = (laneId: string, relX: number, width: number): void => {
    const right = relX + width;
    const prev = laneChildMaxX.get(laneId) ?? LANE.planspaceLanePaddingX;
    if (right > prev) laneChildMaxX.set(laneId, right);
    laneChildCount.set(laneId, (laneChildCount.get(laneId) ?? 0) + 1);
  };

  visibleNodes.forEach((node, index) => {
    const resumeParent = findResumeParent(node, nodeById);
    const isActive = node.id === activeNodeId;
    const stored = layoutHints[node.id];
    const planspaceId = resolvePlanspaceId(node, allNodeById);
    const planspaceColor = colorForPlanspace(
      planspaceId,
      planspaceIndex,
      planspaceColorOverrides,
    );
    if (planspaceId && planspaceColor) laneColors.set(planspaceId, planspaceColor);
    const placeInLane = (spacing: number, width: number, defaultY: number) => {
      const cursor = advanceLane(planspaceId!, spacing);
      const position = stored ?? { x: cursor, y: defaultY };
      recordChildExtent(planspaceId!, position.x, width);
      return position;
    };
    const placeFree = (spacing: number) => {
      const position = stored ?? { x: freeCursorX, y: LANE.timelineY };
      freeCursorX += spacing;
      return position;
    };

    if (node.kind === "op") {
      /* Folded into a chevron edge — skip rendering as a tile. */
      if (opsWithChild.has(node.id)) return;
      const parent = node.parent_node_id ? (nodeById.get(node.parent_node_id) ?? null) : null;
      const position = planspaceId
        ? placeInLane(LANE.opSpacing, LANE.opWidth, LANE.planspaceLaneAgentRowY)
        : placeFree(LANE.opSpacing);
      rfNodes.push({
        id: node.id,
        type: "op",
        position,
        data: { node, parent, child: null },
        draggable: true,
        ...(planspaceId
          ? { parentNode: `planspace:${planspaceId}`, extent: "parent" as const }
          : {}),
      });
    } else {
      const isLastInLane = !hasDescendantById.has(node.id);
      const position = planspaceId
        ? placeInLane(LANE.agentSpacing, LANE.agentWidth, LANE.planspaceLaneAgentRowY)
        : placeFree(LANE.agentSpacing);
      rfNodes.push({
        id: node.id,
        type: "agent",
        position,
        data: {
          node,
          index,
          resumeParent,
          isActive,
          planspaceColor,
          isLastInLane,
          readyToPromote: isVirtualReady(node, nodeById),
        },
        draggable: true,
        ...(planspaceId
          ? { parentNode: `planspace:${planspaceId}`, extent: "parent" as const }
          : {}),
      });
    }

    /* timeline / resume / op-chevron edge from FS-parent */
    const interposedOp = opByChildId.get(node.id);
    if (interposedOp) {
      /* parent → child carrying the folded op chevron */
      const grandparentId =
        interposedOp.parent_node_id && nodeById.has(interposedOp.parent_node_id)
          ? interposedOp.parent_node_id
          : "root";
      rfEdges.push({
        id: `op:${interposedOp.id}->${node.id}`,
        source: grandparentId,
        target: node.id,
        type: "opChevron",
        data: { childState: node.state, op: interposedOp },
      });
    } else if (node.parent_node_id && nodeById.has(node.parent_node_id)) {
      const parentNode = nodeById.get(node.parent_node_id)!;
      const isReview = parentNode.kind === "agent" && node.category === "review";
      const isResume = parentNode.kind !== "op" && node.kind === "agent" && !isReview;
      rfEdges.push({
        id: `tl:${node.parent_node_id}->${node.id}`,
        source: node.parent_node_id,
        target: node.id,
        targetHandle: isReview ? "reviews" : undefined,
        type: isReview ? "reviews" : isResume ? "resume" : "timeline",
        data: { childState: node.state },
      });
    } else if (node.parent_node_id === null || node.parent_node_id === undefined) {
      /* root-anchored timeline edge */
      rfEdges.push({
        id: `tl:root->${node.id}`,
        source: "root",
        target: node.id,
        type: "timeline",
        data: { childState: node.state },
      });
    }
  });

  /* error terminals — a small red-edged downstream node per failed run.
   * The owning agent keeps its own error state; the terminal puts the failure
   * text into the graph itself so retries (resume edges back to the parent)
   * read as a visible loop instead of a banner inside a panel. */
  visibleNodes.forEach((node) => {
    if (node.kind === "op") return;
    if (node.state !== "error") return;
    if (!node.error) return;
    const terminalId = `err:${node.id}`;
    const sourceNode = rfNodes.find((n) => n.id === node.id);
    const baseX = sourceNode?.position.x ?? LANE.rootX;
    const baseY = sourceNode?.position.y ?? LANE.timelineY;
    const stored = layoutHints[terminalId];
    /* Inherit the owner's lane parent so dragging the lane keeps the failure
     * marker tied to its agent. Owner-relative offset stays the same in both
     * regimes. */
    const ownerParent = sourceNode?.parentNode;
    rfNodes.push({
      id: terminalId,
      type: "errorTerminal",
      position: stored ?? {
        /* Drop below the agent so retries (next timeline slot at
         * baseX + agentSpacing) don't stack on top of the failure marker. */
        x: baseX,
        y: baseY + LANE.artifactOffsetY,
      },
      data: {
        ownerNodeId: node.id,
        message: node.error,
      },
      draggable: true,
      selectable: true,
      ...(ownerParent ? { parentNode: ownerParent, extent: "parent" as const } : {}),
    });
    rfEdges.push({
      id: `errtl:${node.id}->${terminalId}`,
      source: node.id,
      target: terminalId,
      type: "timeline",
      data: { childState: "error" as NodeInfo["state"] },
    });
  });

  /* context lane — one node per distinct (scope, kind, path) tuple across all bundles */
  type CtxAggregate = {
    identityKey: string;
    scope: string;
    kind: string;
    path: string;
    chars: number;
    loadedBy: Set<string>;
    plugId?: string | null;
  };
  const ctxAgg = new Map<string, CtxAggregate>();
  for (const [ownerId, bundle] of Object.entries(contextBundlesByNodeId)) {
    const owner = allNodeById.get(ownerId);
    if (owner) {
      const ownerPlanspaceId = resolvePlanspaceId(owner, allNodeById);
      if (ownerPlanspaceId && hiddenPlanspaces.has(ownerPlanspaceId)) continue;
    }
    if (!bundle) continue;
    for (const src of bundle.sources) {
      if (src.plug_id && hiddenPlanspaces.has(src.plug_id)) continue;
      const key = contextIdentityKey(src.scope, src.kind, src.path);
      const existing = ctxAgg.get(key);
      if (existing) {
        existing.loadedBy.add(ownerId);
        if (src.chars > existing.chars) existing.chars = src.chars;
      } else {
        ctxAgg.set(key, {
          identityKey: key,
          scope: src.scope,
          kind: src.kind,
          path: src.path,
          chars: src.chars,
          loadedBy: new Set([ownerId]),
          plugId: src.plug_id ?? null,
        });
      }
    }
  }

  /* Three placement regimes for ctx tiles:
   *   - Project-root scope → neutral top stripe (project-wide reference).
   *   - plugId names a known planspace → joins that lane as a child, so
   *     STATUS/PLAN/CONTEXT live visually inside the planspace they belong to.
   *   - Everything else (skill CONTEXT not bound to a planspace, …) lives in
   *     the floating "loaded context" stripe below the top one.
   * The split keeps project-wide references separate from planspace-owned
   * memory while still showing free-form loads. */
  let projectCtxCursorX = LANE.rootX + 180;
  let laneCtxCursorX = LANE.rootX + 180;
  const inLaneCtxCursor = new Map<string, number>();
  for (const agg of ctxAgg.values()) {
    const ctxId = `ctx:${agg.identityKey}`;
    const stored = layoutHints[ctxId];
    const isProject = agg.scope === "project-root";
    const homeLaneId =
      !isProject && agg.plugId && planspaceOrder.includes(agg.plugId)
        ? agg.plugId
        : null;
    let position: { x: number; y: number };
    let parentNode: string | undefined;
    let extent: "parent" | undefined;
    if (homeLaneId) {
      const cursor =
        inLaneCtxCursor.get(homeLaneId) ?? LANE.planspaceLanePaddingX;
      position = stored ?? { x: cursor, y: LANE.planspaceLaneCtxRowY };
      inLaneCtxCursor.set(homeLaneId, cursor + LANE.planspaceCtxStep);
      parentNode = `planspace:${homeLaneId}`;
      extent = "parent";
      /* Width here matches ContextNode (160 for non-project tiles). */
      recordChildExtent(homeLaneId, position.x, 160);
    } else if (isProject) {
      position = stored ?? { x: projectCtxCursorX, y: LANE.projectContextLaneY };
      projectCtxCursorX += 240;
    } else {
      position = stored ?? { x: laneCtxCursorX, y: LANE.contextLaneY };
      laneCtxCursorX += 180;
    }
    rfNodes.push({
      id: ctxId,
      type: "context",
      position,
      data: {
        identityKey: agg.identityKey,
        scope: agg.scope,
        kind: agg.kind,
        path: agg.path,
        filename: filenameOf(agg.path),
        chars: agg.chars,
        loadedByNodeIds: Array.from(agg.loadedBy),
        plugId: agg.plugId ?? null,
      },
      draggable: true,
      ...(parentNode ? { parentNode, extent } : {}),
    });
    for (const ownerId of agg.loadedBy) {
      rfEdges.push({
        id: `ld:${ctxId}->${ownerId}`,
        source: ctxId,
        target: ownerId,
        type: "loads",
      });
    }
  }

  /* Lane swimlanes. Constructed AFTER both the main child loop and the ctx
   * loop so the per-lane width includes the longest of (agent row, ctx row).
   * Spliced at index 1 so lanes sit right after `root` and before all of
   * their children in the rfNodes array — React Flow requires parents to come
   * before their children. */
  const laneNodes: RFNode[] = [];
  for (const planspaceId of planspaceOrder) {
    const pos = laneAbsPos.get(planspaceId);
    if (!pos) continue;
    const maxRight =
      laneChildMaxX.get(planspaceId) ?? (LANE.planspaceLanePaddingX + LANE.agentWidth);
    const width = Math.max(
      LANE.agentWidth + LANE.planspaceLanePaddingX * 2,
      maxRight + LANE.planspaceLanePaddingX,
    );
    const color =
      laneColors.get(planspaceId) ??
      colorForPlanspace(planspaceId, planspaceIndex, planspaceColorOverrides) ??
      PLANSPACE_PALETTE[0];
    laneNodes.push({
      id: `planspace:${planspaceId}`,
      type: "planspaceLane",
      position: pos,
      data: {
        planspaceId,
        label: labelForPlanspace(planspaceId),
        nodeCount: laneChildCount.get(planspaceId) ?? 0,
        width,
        height: LANE.planspaceLaneHeight,
        color,
        active: planspaceId === activePlanspaceId,
      },
      selectable: true,
      draggable: true,
      dragHandle: ".planspace-lane-drag-handle",
      zIndex: -20,
    });
  }
  rfNodes.splice(1, 0, ...laneNodes);

  /* phantom composer — kept top-level so it can park at the lane's right edge
   * without being clipped by `extent: "parent"`. Its absolute position is
   * computed from the lane's abs pos + the lane's current right boundary. */
  if (phantomFreshStart || phantomFromNodeId !== undefined) {
    const phantomId = "phantom:composer";
    let position: { x: number; y: number };
    let resumeFromLabel: string | null = null;
    if (phantomFromNodeId) {
      const parent = nodeById.get(phantomFromNodeId);
      const parentRf = rfNodes.find((n) => n.id === phantomFromNodeId);
      let parentAbsX = parentRf?.position.x ?? freeCursorX;
      let parentAbsY = parentRf?.position.y ?? LANE.timelineY;
      if (parentRf?.parentNode) {
        const laneId = parentRf.parentNode.slice("planspace:".length);
        const laneAbs = laneAbsPos.get(laneId);
        if (laneAbs) {
          parentAbsX = laneAbs.x + parentAbsX;
          parentAbsY = laneAbs.y + parentAbsY;
        }
      }
      position = { x: parentAbsX + LANE.agentSpacing, y: parentAbsY };
      resumeFromLabel = parent
        ? (parent.summary || parent.prompt || parent.id.slice(0, 8)).slice(0, 48)
        : null;
    } else if (
      activePlanspaceId &&
      !hiddenPlanspaces.has(activePlanspaceId) &&
      laneAbsPos.has(activePlanspaceId)
    ) {
      const laneAbs = laneAbsPos.get(activePlanspaceId)!;
      const maxRight =
        laneChildMaxX.get(activePlanspaceId) ?? LANE.planspaceLanePaddingX;
      position = {
        x: laneAbs.x + maxRight + LANE.planspaceLanePaddingX,
        y: laneAbs.y + LANE.planspaceLaneAgentRowY,
      };
    } else {
      position = { x: freeCursorX, y: LANE.timelineY };
    }
    rfNodes.push({
      id: phantomId,
      type: "phantom",
      position,
      data: {
        resumeFromNodeId: phantomFromNodeId ?? null,
        resumeFromLabel,
        disabled: phantomDisabled,
      },
      draggable: false,
      selectable: true,
    });
  }

  return { rfNodes, rfEdges };
}

/* ───────── helpers ───────── */

export function contextIdentityKey(scope: string, kind: string, path: string): string {
  return `${scope}::${kind}::${path}`;
}

export function filenameOf(p: string): string {
  if (!p) return "(unnamed)";
  const norm = p.replace(/\\/g, "/");
  const idx = norm.lastIndexOf("/");
  return idx >= 0 ? norm.slice(idx + 1) : norm;
}

export function findResumeParent(
  node: NodeInfo,
  byId: Map<string, NodeInfo>,
): NodeInfo | null {
  if (!node.parent_node_id) return null;
  const parent = byId.get(node.parent_node_id);
  if (!parent || parent.kind === "op") return null;
  return parent;
}

function collectPlanspaceOrder(
  nodes: NodeInfo[],
  byId: Map<string, NodeInfo>,
): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const node of nodes) {
    const id = resolvePlanspaceId(node, byId);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

function resolvePlanspaceId(
  node: NodeInfo,
  byId: Map<string, NodeInfo>,
): string | null {
  if (node.planspace_id) return node.planspace_id;
  const snapshotValue = node.settings_snapshot?.active_planspace_id;
  if (typeof snapshotValue === "string" && snapshotValue) return snapshotValue;
  if (node.parent_node_id) {
    const parent = byId.get(node.parent_node_id);
    if (parent) return resolvePlanspaceId(parent, byId);
  }
  return null;
}

function initialFreeCursorX(
  planspaceOrder: string[],
  laneAbsPos: Map<string, { x: number; y: number }>,
  visibleNodes: NodeInfo[],
  byId: Map<string, NodeInfo>,
  opsWithChild: Set<string>,
  layoutHints: Record<string, { x: number; y: number }>,
): number {
  const base = LANE.rootX + 180;
  const firstLaneId = planspaceOrder[0];
  if (!firstLaneId) return base;
  const firstLaneAbs = laneAbsPos.get(firstLaneId);
  if (!firstLaneAbs) return base;

  let laneCursor = LANE.planspaceLanePaddingX;
  let occupiedRight = LANE.planspaceLanePaddingX + LANE.agentWidth;
  for (const node of visibleNodes) {
    if (resolvePlanspaceId(node, byId) !== firstLaneId) continue;
    const geometry = renderedWorkNodeGeometry(node, opsWithChild);
    if (!geometry) continue;
    const position = layoutHints[node.id] ?? {
      x: laneCursor,
      y: LANE.planspaceLaneAgentRowY,
    };
    occupiedRight = Math.max(occupiedRight, position.x + geometry.width);
    laneCursor += geometry.spacing;
  }

  /* Lane 0's agent row has the same absolute y as top-level/free nodes.
   * Starting the free cursor after that row prevents mixed sessions from
   * placing a free tile directly over the first in-lane tile. */
  const nextLaneCursor = Math.max(
    laneCursor,
    LANE.planspaceLanePaddingX + LANE.agentSpacing,
  );
  return Math.max(
    base,
    firstLaneAbs.x + nextLaneCursor,
    firstLaneAbs.x + occupiedRight + LANE.planspaceLanePaddingX,
  );
}

function renderedWorkNodeGeometry(
  node: NodeInfo,
  opsWithChild: Set<string>,
): { spacing: number; width: number } | null {
  if (node.kind === "op") {
    if (opsWithChild.has(node.id)) return null;
    return { spacing: LANE.opSpacing, width: LANE.opWidth };
  }
  return { spacing: LANE.agentSpacing, width: LANE.agentWidth };
}

function isVirtualReady(
  node: NodeInfo,
  byId: Map<string, NodeInfo>,
): boolean {
  if (node.state !== "virtual" || node.obsolete_reason) return false;
  for (const depId of node.scheduled_deps ?? []) {
    const dep = byId.get(depId);
    if (!dep) continue;
    if (dep.state === "done" || dep.state === "error" || dep.state === "cancelled") {
      continue;
    }
    if (dep.state === "virtual" && dep.obsolete_reason) continue;
    return false;
  }
  return true;
}

function colorForPlanspace(
  planspaceId: string | null,
  planspaceIndex: Map<string, number>,
  overrides: Map<string, string> = new Map(),
): PlanspaceColor | null {
  if (!planspaceId) return null;
  const named = overrides.get(planspaceId);
  if (named) {
    const match = PLANSPACE_PALETTE.find((c) => c.name === named);
    if (match) return match;
  }
  const index = planspaceIndex.get(planspaceId) ?? 0;
  return PLANSPACE_PALETTE[index % PLANSPACE_PALETTE.length];
}

function collectPlanspaceColorOverrides(
  contextBundlesByNodeId: Record<string, ContextBundle | null | undefined>,
): Map<string, string> {
  const out = new Map<string, string>();
  for (const bundle of Object.values(contextBundlesByNodeId)) {
    if (!bundle) continue;
    const active = bundle.active_planspace;
    if (
      active &&
      typeof active.id === "string" &&
      typeof (active as { color?: unknown }).color === "string" &&
      !out.has(active.id)
    ) {
      out.set(active.id, (active as { color: string }).color);
    }
  }
  return out;
}

function labelForPlanspace(planspaceId: string): string {
  const raw = planspaceId.includes(".")
    ? planspaceId.slice(planspaceId.indexOf(".") + 1)
    : planspaceId;
  return raw
    .split(/[-_]/g)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}
