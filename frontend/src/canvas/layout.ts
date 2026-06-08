import type { Edge, Node } from "reactflow";
import type { ContextBundle, NodeInfo } from "../types";

/* ───────── canvas node payloads ───────── */

export type CrossLaneLoad = {
  planspaceId: string;
  label: string;
  color: PlanspaceColor;
};

export type AgentNodeData = {
  node: NodeInfo;
  index: number;
  resumeParent: NodeInfo | null;
  /** true when this agent is currently streaming text in the live channel */
  isActive: boolean;
  planspaceColor: PlanspaceColor | null;
  crossLaneLoads: CrossLaneLoad[];
};

export type GateNodeData = AgentNodeData;

export type OpNodeData = {
  node: NodeInfo;
  parent: NodeInfo | null;
  child: NodeInfo | null;
};

export type ErrorTerminalData = {
  /** The owning agent / gate node whose error this surfaces. */
  ownerNodeId: string;
  ownerKind: NodeInfo["kind"];
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
};

export type RFNodeData =
  | AgentNodeData
  | GateNodeData
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
  gateSpacing: 240,
  planspaceLaneSpacing: 220,
  planspaceLanePaddingX: 28,
  planspaceLaneTopPad: 44,
  planspaceLaneHeight: 164,
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
  } = args;

  const rfNodes: RFNode[] = [];
  const rfEdges: RFEdge[] = [];
  const nodeById = new Map<string, NodeInfo>();
  for (const n of nodes) nodeById.set(n.id, n);
  const planspaceOrder = collectPlanspaceOrder(nodes, nodeById);
  const planspaceIndex = new Map(planspaceOrder.map((id, index) => [id, index]));
  const laneBounds = new Map<
    string,
    {
      minX: number;
      maxX: number;
      y: number;
      count: number;
      color: PlanspaceColor;
    }
  >();

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
  for (const node of nodes) {
    if (node.kind !== "op") continue;
    const childIdx = nodes.findIndex(
      (n) => n.parent_node_id === node.id && n.kind !== "op",
    );
    if (childIdx >= 0) {
      opByChildId.set(nodes[childIdx].id, node);
      opsWithChild.add(node.id);
    }
  }

  const planspaceColorOverrides = collectPlanspaceColorOverrides(
    contextBundlesByNodeId,
  );

  /* main timeline: agents, gates, ops along x = index*spacing */
  let cursorX = LANE.rootX + 180;
  nodes.forEach((node, index) => {
    const resumeParent = findResumeParent(node, nodeById);
    const isActive = node.id === activeNodeId;
    const stored = layoutHints[node.id];
    const planspaceId = resolvePlanspaceId(node, nodeById);
    const laneY = yForPlanspace(planspaceId, planspaceIndex);
    const planspaceColor = colorForPlanspace(
      planspaceId,
      planspaceIndex,
      planspaceColorOverrides,
    );
    const crossLaneLoads = collectCrossLaneLoads(
      node,
      planspaceId,
      contextBundlesByNodeId[node.id],
      planspaceIndex,
      planspaceColorOverrides,
    );

    if (node.kind === "op") {
      /* Folded into a chevron edge — skip rendering as a tile. */
      if (opsWithChild.has(node.id)) return;
      const parent = node.parent_node_id ? (nodeById.get(node.parent_node_id) ?? null) : null;
      const position = stored ?? { x: cursorX, y: laneY };
      rfNodes.push({
        id: node.id,
        type: "op",
        position,
        data: { node, parent, child: null },
        draggable: true,
      });
      recordLaneBounds(laneBounds, planspaceId, position, LANE.opWidth, planspaceIndex, planspaceColorOverrides);
      cursorX += LANE.opSpacing;
    } else if (node.kind === "gate") {
      const position = stored ?? { x: cursorX, y: laneY };
      rfNodes.push({
        id: node.id,
        type: "gate",
        position,
        data: { node, index, resumeParent, isActive, planspaceColor, crossLaneLoads },
        draggable: true,
      });
      recordLaneBounds(laneBounds, planspaceId, position, 200, planspaceIndex, planspaceColorOverrides);
      cursorX += LANE.gateSpacing;
    } else {
      const position = stored ?? { x: cursorX, y: laneY };
      rfNodes.push({
        id: node.id,
        type: "agent",
        position,
        data: { node, index, resumeParent, isActive, planspaceColor, crossLaneLoads },
        draggable: true,
      });
      recordLaneBounds(laneBounds, planspaceId, position, LANE.agentWidth, planspaceIndex, planspaceColorOverrides);
      cursorX += LANE.agentSpacing;
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
      const isReview = parentNode.kind === "agent" && node.kind === "gate";
      const isResume = parentNode.kind !== "op" && node.kind === "agent";
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

  const laneNodes: RFNode[] = [];
  for (const [planspaceId, bounds] of laneBounds) {
    const x = bounds.minX - LANE.planspaceLanePaddingX;
    const y = bounds.y - LANE.planspaceLaneTopPad;
    const width = Math.max(
      LANE.agentWidth + LANE.planspaceLanePaddingX * 2,
      bounds.maxX - bounds.minX + LANE.planspaceLanePaddingX * 2,
    );
    laneNodes.push({
      id: `planspace:${planspaceId}`,
      type: "planspaceLane",
      position: { x, y },
      data: {
        planspaceId,
        label: labelForPlanspace(planspaceId),
        nodeCount: bounds.count,
        width,
        height: LANE.planspaceLaneHeight,
        color: bounds.color,
      },
      selectable: true,
      draggable: false,
      zIndex: -20,
    });
  }
  rfNodes.splice(1, 0, ...laneNodes);

  /* error terminals — a small red-edged downstream node per failed run.
   * The owning agent keeps its own error state; the terminal puts the failure
   * text into the graph itself so retries (resume edges back to the parent)
   * read as a visible loop instead of a banner inside a panel. */
  nodes.forEach((node) => {
    if (node.kind === "op") return;
    if (node.state !== "error") return;
    if (!node.error) return;
    const terminalId = `err:${node.id}`;
    const sourceNode = rfNodes.find((n) => n.id === node.id);
    const baseX = sourceNode?.position.x ?? LANE.rootX;
    const baseY = sourceNode?.position.y ?? LANE.timelineY;
    const stored = layoutHints[terminalId];
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
        ownerKind: node.kind,
        message: node.error,
      },
      draggable: true,
      selectable: true,
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
  };
  const ctxAgg = new Map<string, CtxAggregate>();
  for (const [ownerId, bundle] of Object.entries(contextBundlesByNodeId)) {
    if (!bundle) continue;
    for (const src of bundle.sources) {
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
        });
      }
    }
  }

  /* Project-root CONTEXT.md occupies its own neutral top stripe; everything
   * else (planspace STATUS/PLAN, skill CONTEXT, …) lives in the colored
   * context lane below it. The split makes "plan-free reference" vs
   * "planspace state" visually distinct. */
  let projectCtxCursorX = LANE.rootX + 180;
  let laneCtxCursorX = LANE.rootX + 180;
  for (const agg of ctxAgg.values()) {
    const ctxId = `ctx:${agg.identityKey}`;
    const stored = layoutHints[ctxId];
    const isProject = agg.scope === "project-root";
    const fallbackPos = isProject
      ? { x: projectCtxCursorX, y: LANE.projectContextLaneY }
      : { x: laneCtxCursorX, y: LANE.contextLaneY };
    rfNodes.push({
      id: ctxId,
      type: "context",
      position: stored ?? fallbackPos,
      data: {
        identityKey: agg.identityKey,
        scope: agg.scope,
        kind: agg.kind,
        path: agg.path,
        filename: filenameOf(agg.path),
        chars: agg.chars,
        loadedByNodeIds: Array.from(agg.loadedBy),
      },
      draggable: true,
    });
    if (isProject) {
      projectCtxCursorX += 240;
    } else {
      laneCtxCursorX += 180;
    }
    for (const ownerId of agg.loadedBy) {
      rfEdges.push({
        id: `ld:${ctxId}->${ownerId}`,
        source: ctxId,
        target: ownerId,
        type: "loads",
      });
    }
  }

  /* planspace-update arrows — when an agent wrote back into a context node
   * (planspace), draw a +Δ edge from the agent to that context node.
   * The source-of-truth is `settings_snapshot.planspace_update.planspace_id`,
   * resolved against the agent's own bundle so we can map planspace id →
   * the context node id we materialized above. */
  for (const node of nodes) {
    if (node.kind !== "agent") continue;
    const update = (node.settings_snapshot?.planspace_update
      ?? node.settings_snapshot?.memory_delta) as
      | { planspace_id?: string; applied?: number; proposed?: number }
      | undefined;
    if (!update || !update.planspace_id) continue;
    if (!(update.applied ?? 0) && !(update.proposed ?? 0)) continue;
    const bundle = contextBundlesByNodeId[node.id];
    if (!bundle) continue;
    const src = bundle.sources.find((s) => s.plug_id === update.planspace_id);
    if (!src) continue;
    const ctxId = `ctx:${contextIdentityKey(src.scope, src.kind, src.path)}`;
    rfEdges.push({
      id: `md:${node.id}->${ctxId}`,
      source: node.id,
      target: ctxId,
      targetHandle: "writes",
      type: "memoryDelta",
      data: {
        applied: update.applied ?? 0,
        proposed: update.proposed ?? 0,
      },
    });
  }

  /* phantom composer */
  if (phantomFreshStart || phantomFromNodeId !== undefined) {
    const phantomId = "phantom:composer";
    let position: { x: number; y: number };
    let resumeFromLabel: string | null = null;
    if (phantomFromNodeId) {
      const parent = nodeById.get(phantomFromNodeId);
      const parentRf = rfNodes.find((n) => n.id === phantomFromNodeId);
      const parentX = parentRf?.position.x ?? cursorX;
      const parentY = parentRf?.position.y ?? LANE.timelineY;
      position = { x: parentX + LANE.agentSpacing, y: parentY };
      resumeFromLabel = parent
        ? (parent.summary || parent.prompt || parent.id.slice(0, 8)).slice(0, 48)
        : null;
    } else {
      position = { x: cursorX, y: LANE.timelineY };
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
      selectable: false,
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

function yForPlanspace(
  planspaceId: string | null,
  planspaceIndex: Map<string, number>,
): number {
  if (!planspaceId) return LANE.timelineY;
  return LANE.timelineY + (planspaceIndex.get(planspaceId) ?? 0) * LANE.planspaceLaneSpacing;
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

function collectCrossLaneLoads(
  node: NodeInfo,
  ownPlanspaceId: string | null,
  bundle: ContextBundle | null | undefined,
  planspaceIndex: Map<string, number>,
  overrides: Map<string, string>,
): CrossLaneLoad[] {
  if (!bundle) return [];
  const seen = new Set<string>();
  const out: CrossLaneLoad[] = [];
  for (const src of bundle.sources) {
    const plugId = src.plug_id;
    if (!plugId || !plugId.startsWith("planspaces.")) continue;
    if (plugId === ownPlanspaceId) continue;
    if (seen.has(plugId)) continue;
    seen.add(plugId);
    const color = colorForPlanspace(plugId, planspaceIndex, overrides);
    if (!color) continue;
    out.push({
      planspaceId: plugId,
      label: labelForPlanspace(plugId),
      color,
    });
  }
  // Silence unused-arg warning when node has nothing to add beyond its bundle.
  void node;
  return out;
}

function recordLaneBounds(
  laneBounds: Map<
    string,
    {
      minX: number;
      maxX: number;
      y: number;
      count: number;
      color: PlanspaceColor;
    }
  >,
  planspaceId: string | null,
  position: { x: number; y: number },
  width: number,
  planspaceIndex: Map<string, number>,
  overrides: Map<string, string> = new Map(),
): void {
  if (!planspaceId) return;
  const color = colorForPlanspace(planspaceId, planspaceIndex, overrides);
  if (!color) return;
  const existing = laneBounds.get(planspaceId);
  const maxX = position.x + width;
  if (existing) {
    existing.minX = Math.min(existing.minX, position.x);
    existing.maxX = Math.max(existing.maxX, maxX);
    existing.count += 1;
    return;
  }
  laneBounds.set(planspaceId, {
    minX: position.x,
    maxX,
    y: position.y,
    count: 1,
    color,
  });
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
