import type { CoordinateExtent, Edge, Node, NodeChange } from "reactflow";
import type { ArtifactRef, CommitDescriptor, ContextBundle, NodeInfo } from "../types";

/* ───────── canvas node payloads ───────── */

export type AgentNodeData = {
  node: NodeInfo;
  resumeParent: NodeInfo | null;
  /** true when this node is currently active in the project runner */
  isActive: boolean;
  planspaceColor: PlanspaceColor | null;
  /** true when no agent node (in any lane) has this one as its agent parent */
  isLastInLane: boolean;
  readyToPromote: boolean;
  canCreateVirtual: boolean;
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

export type ArtifactNodeData = {
  ownerNodeId: string;
  artifact: ArtifactRef | null;
  overflowCount: number;
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
  /** source plug id when this context file comes from a planspace/principle plug */
  plugId?: string | null;
  /** manifest title, populated for known principles so tooltips read as a name */
  title?: string | null;
  usedByNodeIds?: string[];
};

/** Minimal projection of a user-wide principle for buildGraph enumeration.
 *  Kept local to avoid a layout → api dependency. */
export type PrincipleEnumeration = {
  id: string;
  slug: string;
  title: string;
  description: string | null;
  path: string;
};

export type SkillEnumeration = {
  id: string;
  slug: string;
  title: string;
  description: string;
  path: string;
};

export type CommitNodeData = {
  commit: CommitDescriptor;
  head: boolean;
  ghost?: boolean;
  dirtyCount?: number;
  externalCountBefore?: number;
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
  canCreateVirtual: boolean;
};

export type RFNodeData =
  | AgentNodeData
  | OpNodeData
  | ContextNodeData
  | CommitNodeData
  | PlanspaceLaneData
  | ErrorTerminalData
  | ArtifactNodeData;

export type RFNode = Node<RFNodeData>;
export type RFEdge = Edge;

/* ───────── geometry ───────── */

export const LANE = {
  rootX: 40,
  timelineY: 220,
  trunkX: 40,
  trunkStartY: 80,
  trunkStep: 112,
  trunkGutter: 220,
  projectContextLaneY: 8,
  contextLaneY: 110,
  errorTerminalOffsetY: 140,
  artifactOffsetY: 140,
  agentWidth: 224,
  agentHeight: 132,
  agentSpacing: 280,
  opWidth: 96,
  opHeight: 80,
  opSpacing: 140,
  contextHeight: 80,
  siblingYStep: 152,
  /* Lane is laid out vertically as: header band → ctx row → agent row → bottom pad.
   * Y values below are RELATIVE positions inside the lane (origin = lane top-left). */
  planspaceLaneSpacing: 360,
  planspaceLanePaddingX: 40,
  planspaceLanePaddingY: 40,
  planspaceLaneBottomPadding: 20,
  planspaceLaneCtxRowY: 48,
  planspaceLaneAgentRowY: 128,
  planspaceLaneHeight: 280,
  /* Horizontal step between ctx tiles inside a lane (tile width ~160 + gap). */
  planspaceCtxStep: 180,
};

const PLANSPACE_CHILD_EXTENT: CoordinateExtent = [
  [LANE.planspaceLanePaddingX, LANE.planspaceLanePaddingY],
  [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY],
];

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
  /** ids of project runners that currently occupy execution slots */
  activeNodeIds: string[];
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
  /** true when the active lane's virtual create button should be enabled */
  canCreateVirtual: boolean;
  /** User-wide principles, used to resolve bound ids to paths and titles. */
  principles?: PrincipleEnumeration[];
  /** Native Agent Skills enumerated from GET /skills. */
  skills?: SkillEnumeration[];
  gitCommits?: CommitDescriptor[];
  gitHead?: string | null;
  gitDirtyCount?: number;
};

export type BuildGraphResult = {
  rfNodes: RFNode[];
  rfEdges: RFEdge[];
  epochMembersByCommitSha: Record<string, string[]>;
  commitHubIdByNodeId: Record<string, string>;
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
    activeNodeIds,
    layoutHints,
    contextBundlesByNodeId,
    knownPlanspaceIds,
    hiddenPlanspaceIds,
    activePlanspaceId,
    canCreateVirtual,
    principles = [],
    skills = [],
    gitCommits = [],
    gitHead = null,
    gitDirtyCount = 0,
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
  const planspaceOrder: string[] = [];
  for (const id of knownPlanspaceIds) {
    if (!id || hiddenPlanspaces.has(id) || planspaceOrder.includes(id)) continue;
    planspaceOrder.push(id);
  }
  for (const id of collectPlanspaceOrder(visibleNodes, allNodeById)) {
    if (planspaceOrder.includes(id)) continue;
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
      x: LANE.rootX + LANE.trunkGutter,
      y: LANE.timelineY + idx * LANE.planspaceLaneSpacing - LANE.planspaceLaneAgentRowY,
    };
    laneAbsPos.set(id, layoutHints[`planspace:${id}`] ?? defaultPos);
  }
  /* Per-lane growable width + node count, harvested during the child pass. */
  const laneChildMaxX = new Map<string, number>();
  const laneChildMaxY = new Map<string, number>();
  const laneChildCount = new Map<string, number>();
  const laneColors = new Map<string, PlanspaceColor>();
  const nodeRelativePositions = new Map<string, { x: number; y: number }>();
  const branchSiblingCounts = new Map<string, number>();

  gitCommits.forEach((commit, index) => {
    rfNodes.push({
      id: `commit:${commit.sha}`,
      type: "commit",
      position: layoutHints[`commit:${commit.sha}`] ?? {
        x: LANE.trunkX,
        y: LANE.trunkStartY + index * LANE.trunkStep,
      },
      width: 76,
      height: 76,
      data: {
        commit,
        head: commit.sha === gitHead,
        externalCountBefore: index === 0 ? commit.external_count_before : 0,
      },
      draggable: true,
      selectable: true,
    });
    if (index > 0) {
      const previous = `commit:${gitCommits[index - 1].sha}`;
      rfEdges.push({
        id: `commit-trunk:${previous}:${commit.sha}`,
        source: previous,
        target: `commit:${commit.sha}`,
        type: "commitTrunk",
        data: { externalCount: commit.external_count_before },
      });
    }
  });
  if (gitDirtyCount > 0) {
    const ghostId = "commit:ghost";
    rfNodes.push({ id: ghostId, type: "commit", position: layoutHints[ghostId] ?? { x: LANE.trunkX, y: LANE.trunkStartY + gitCommits.length * LANE.trunkStep }, width: 76, height: 76, data: { commit: { sha: "ghost", live: false, message: "Uncommitted changes", external_count_before: 0, aliases: [] }, head: false, ghost: true, dirtyCount: gitDirtyCount }, draggable: true, selectable: true });
    const previous = gitCommits.at(-1);
    if (previous) {
      rfEdges.push({ id: `commit-trunk:${previous.sha}:ghost`, source: `commit:${previous.sha}`, target: ghostId, type: "commitTrunk" });
    }
  }

  const planspaceColorOverrides = collectPlanspaceColorOverrides(
    contextBundlesByNodeId,
  );

  /* Mark agent nodes that no other work node depends on or continues from. The
   * "+" hover affordance only appears on these tail tiles. */
  const hasDescendantById = new Set<string>();
  for (const candidate of visibleNodes) {
    if (candidate.kind === "op") continue;
    for (const depId of visibleScheduledDepIds(candidate, nodeById)) {
      hasDescendantById.add(depId);
    }
    const continueSourceId = findContinueSourceId(candidate, nodeById);
    if (continueSourceId) {
      hasDescendantById.add(continueSourceId);
    }
  }

  const commitForSha = (sha: string | null | undefined) =>
    sha ? gitCommits.find((commit) => commit.sha === sha || commit.aliases.includes(sha)) : undefined;
  const epochMembers = new Map<string, NodeInfo[]>();
  for (const node of visibleNodes) {
    if (node.kind === "op" || !node.commit_before) continue;
    const epoch = commitForSha(node.commit_before);
    if (!epoch) continue;
    epochMembers.set(epoch.sha, [...(epochMembers.get(epoch.sha) ?? []), node]);
  }
  const epochMembersByCommitSha: Record<string, string[]> = {};
  const commitHubIdByNodeId: Record<string, string> = {};
  for (const [epochSha, members] of epochMembers) {
    epochMembersByCommitSha[epochSha] = members.map((member) => member.id);
    for (const member of members) {
      commitHubIdByNodeId[member.id] = `commit:${epochSha}`;
    }
    const ids = new Set(members.map((member) => member.id));
    const incoming = new Set<string>();
    const outgoing = new Set<string>();
    for (const member of members) {
      const predecessors = [
        ...(member.scheduled_deps ?? []),
        member.resume_from_node_id,
        member.parent_node_id,
      ].filter((id): id is string => !!id && ids.has(id));
      if (predecessors.length) incoming.add(member.id);
      for (const predecessor of predecessors) outgoing.add(predecessor);
    }
    /* Epoch links cross the lane stack from the trunk column, so they use the
     * agent tile's vertical handles (`epochIn` top / `epochOut` bottom) rather
     * than the horizontal dep/resume axis. */
    for (const member of members) {
      if (!incoming.has(member.id)) {
        rfEdges.push({ id: `commit-source:${epochSha}:${member.id}`, source: `commit:${epochSha}`, target: member.id, targetHandle: "epochIn", type: "commitLink", data: { dashed: true } });
      }
      if (outgoing.has(member.id) || !member.commit_after) continue;
      const after = commitForSha(member.commit_after);
      const epochIndex = gitCommits.findIndex((commit) => commit.sha === epochSha);
      let target = after && after.sha !== epochSha ? `commit:${after.sha}` : null;
      if (!target && epochIndex >= 0) {
        const next = gitCommits.slice(epochIndex + 1).find((commit) => commit.live);
        if (next) target = `commit:${next.sha}`;
      }
      if (!target && gitDirtyCount > 0) target = "commit:ghost";
      if (target) rfEdges.push({ id: `commit-sink:${member.id}:${target}`, source: member.id, sourceHandle: "epochOut", target, type: "commitLink", data: { dashed: true } });
    }
  }

  /* Main timeline. Two coordinate regimes coexist:
   *   - Nodes WITH a planspace become children of `planspace:<id>` lanes:
   *     position is relative to lane, advanced by a per-lane cursor.
   *   - Nodes WITHOUT a planspace stay top-level in absolute coords,
   *     advanced by `freeCursorX`.
   * A one-sided extent protects the lane header and left padding without
   * imposing a right/bottom wall. `parentNode` remains coordinate nesting;
   * planspace membership continues to come from backend `planspace_id`. */
  let freeCursorX = initialFreeCursorX(
    planspaceOrder,
    laneAbsPos,
    visibleNodes,
    allNodeById,
    layoutHints,
  );
  const laneCursors = new Map<string, number>();
  const nextLanePosition = (
    laneId: string,
    spacing: number,
    stored: { x: number; y: number } | undefined,
    defaultY: number,
  ): { x: number; y: number } => {
    const cursor = laneCursors.get(laneId) ?? LANE.planspaceLanePaddingX;
    const position = stored ?? { x: cursor, y: defaultY };
    /* Advance from the position that is actually on the canvas. When older
     * nodes have been rearranged into rows, counting their historical slots
     * would send the next unanchored node far beyond the visible layout. */
    laneCursors.set(laneId, Math.max(cursor, position.x + spacing));
    return position;
  };
  const recordChildExtent = (
    laneId: string,
    relX: number,
    relY: number,
    width: number,
    height: number,
  ): void => {
    const right = relX + width;
    const bottom = relY + height;
    const prev = laneChildMaxX.get(laneId) ?? LANE.planspaceLanePaddingX;
    if (right > prev) laneChildMaxX.set(laneId, right);
    const prevBottom = laneChildMaxY.get(laneId) ?? LANE.planspaceLaneAgentRowY + LANE.agentHeight;
    if (bottom > prevBottom) laneChildMaxY.set(laneId, bottom);
    laneChildCount.set(laneId, (laneChildCount.get(laneId) ?? 0) + 1);
  };

  visibleNodes.forEach((node) => {
    const resumeParent = findResumeParent(node, nodeById);
    const isActive = activeNodeIds.includes(node.id);
    const stored = layoutHints[node.id];
    const planspaceId = resolvePlanspaceId(node, allNodeById);
    const planspaceColor = colorForPlanspace(
      planspaceId,
      planspaceIndex,
      planspaceColorOverrides,
    );
    if (planspaceId && planspaceColor) laneColors.set(planspaceId, planspaceColor);
    const placeInLane = (
      spacing: number,
      width: number,
      height: number,
      defaultY: number,
    ) => {
      const position = nextLanePosition(
        planspaceId!,
        spacing,
        stored,
        defaultY,
      );
      recordChildExtent(planspaceId!, position.x, position.y, width, height);
      nodeRelativePositions.set(node.id, position);
      return position;
    };
    const placeAnchoredVirtualInLane = (
      anchorId: string | null,
    ): { x: number; y: number } | null => {
      if (!planspaceId || !anchorId) {
        return null;
      }
      if (stored) {
        recordChildExtent(
          planspaceId,
          stored.x,
          stored.y,
          LANE.agentWidth,
          LANE.agentHeight,
        );
        nodeRelativePositions.set(node.id, stored);
        return stored;
      }
      const anchorPosition = nodeRelativePositions.get(anchorId);
      if (!anchorPosition) return null;
      const key = `${planspaceId}:${anchorId}`;
      const siblingIndex = branchSiblingCounts.get(key) ?? 0;
      branchSiblingCounts.set(key, siblingIndex + 1);
      const position = {
        x: anchorPosition.x,
        y: anchorPosition.y + LANE.siblingYStep * (siblingIndex + 1),
      };
      recordChildExtent(
        planspaceId,
        position.x,
        position.y,
        LANE.agentWidth,
        LANE.agentHeight,
      );
      nodeRelativePositions.set(node.id, position);
      return position;
    };
    const placeFree = (spacing: number) => {
      const position = stored ?? { x: freeCursorX, y: LANE.timelineY };
      freeCursorX += spacing;
      return position;
    };

    if (node.kind === "op") {
      if (node.state === "done") return;
      const parent = node.parent_node_id ? (nodeById.get(node.parent_node_id) ?? null) : null;
      const position = planspaceId
        ? placeInLane(LANE.opSpacing, LANE.opWidth, LANE.opHeight, LANE.planspaceLaneAgentRowY)
        : placeFree(LANE.opSpacing);
      rfNodes.push({
        id: node.id,
        type: "op",
        position,
        width: LANE.opWidth,
        height: 48,
        data: { node, parent, child: null },
        draggable: true,
        ...(planspaceId
          ? { parentNode: `planspace:${planspaceId}`, extent: PLANSPACE_CHILD_EXTENT }
          : {}),
      });
    } else {
      const isLastInLane = !hasDescendantById.has(node.id);
      const branchAnchorId =
        node.state === "virtual" ? virtualBranchAnchorId(node, nodeById) : null;
      const position = planspaceId
        ? (
            placeAnchoredVirtualInLane(branchAnchorId) ??
            placeInLane(
              LANE.agentSpacing,
              LANE.agentWidth,
              LANE.agentHeight,
              LANE.planspaceLaneAgentRowY,
            )
          )
        : placeFree(LANE.agentSpacing);
      rfNodes.push({
        id: node.id,
        type: "agent",
        position,
        width: LANE.agentWidth,
        height: 86,
        data: {
          node,
          resumeParent,
          isActive,
          planspaceColor,
          isLastInLane,
          readyToPromote: isVirtualReady(node, nodeById),
          canCreateVirtual,
        },
        draggable: true,
        ...(planspaceId
          ? { parentNode: `planspace:${planspaceId}`, extent: PLANSPACE_CHILD_EXTENT }
          : {}),
      });
    }

  });

  /* Dependency arrows are only the planning/template DAG declared by
   * scheduled_deps. A node without deps has no fabricated incoming edge. */
  const continueSourceByNodeId = new Map<string, string>();
  for (const node of visibleNodes) {
    if (node.kind === "op") continue;
    const continueSourceId = findContinueSourceId(node, nodeById);
    if (continueSourceId) continueSourceByNodeId.set(node.id, continueSourceId);
  }

  for (const node of visibleNodes) {
    if (node.kind === "op") continue;
    const visibleDeps = visibleScheduledDepIds(node, nodeById);
    const continueSourceId = continueSourceByNodeId.get(node.id);
    for (const depId of visibleDeps) {
      rfEdges.push({
        id: `dep:${depId}->${node.id}`,
        source: depId,
        target: node.id,
        type: "dependency",
        data: {
          childState: node.state,
          overlapsContinue: depId === continueSourceId,
        },
      });
    }
  }

  /* Continue arrows — explicit provider-conversation continuation. Prefer the
   * virtual/template field, but fall back to parent_node_id for older/directly
   * launched continuation runs that materialized before that field was set. */
  for (const node of visibleNodes) {
    if (node.kind === "op") continue;
    const sourceId = continueSourceByNodeId.get(node.id);
    if (!sourceId) continue;
    rfEdges.push({
      id: `continue:${sourceId}->${node.id}`,
      source: sourceId,
      target: node.id,
      type: "resume",
      data: { childState: node.state },
    });
  }

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
    const terminalPosition = stored ?? {
      /* Drop below the agent so retries (next timeline slot at
       * baseX + agentSpacing) don't stack on top of the failure marker. */
      x: baseX,
      y: baseY + LANE.errorTerminalOffsetY,
    };
    if (ownerParent?.startsWith("planspace:")) {
      recordChildExtent(
        ownerParent.slice("planspace:".length),
        terminalPosition.x,
        terminalPosition.y,
        180,
        88,
      );
    }
    rfNodes.push({
      id: terminalId,
      type: "errorTerminal",
      position: terminalPosition,
      width: 180,
      height: 88,
      data: {
        ownerNodeId: node.id,
        message: node.error,
      },
      draggable: true,
      selectable: true,
      ...(ownerParent ? { parentNode: ownerParent, extent: PLANSPACE_CHILD_EXTENT } : {}),
    });
    rfEdges.push({
      id: `errtl:${node.id}->${terminalId}`,
      source: node.id,
      target: terminalId,
      type: "timeline",
      data: { childState: "error" as NodeInfo["state"] },
    });
  });

  /* Published artifacts — terminal-only tiles fanned beneath their producer. */
  visibleNodes.forEach((node) => {
    if (node.kind === "op") return;
    const published = (node.artifacts ?? []).filter((ref) => ref.status === "published");
    if (published.length === 0) return;
    const visibleArtifacts = published.length <= 4 ? published : published.slice(0, 3);
    const tileCount = visibleArtifacts.length + (published.length > 4 ? 1 : 0);
    const sourceNode = rfNodes.find((candidate) => candidate.id === node.id);
    const baseX = sourceNode?.position.x ?? LANE.rootX;
    const baseY = sourceNode?.position.y ?? LANE.timelineY;
    const ownerParent = sourceNode?.parentNode;
    const centeredStart = baseX + LANE.agentWidth / 2 - (tileCount * 170 - 10) / 2;
    const startX = ownerParent
      ? Math.max(LANE.planspaceLanePaddingX, centeredStart)
      : centeredStart;
    const entries: Array<{ artifact: ArtifactRef | null; overflowCount: number }> = [
      ...visibleArtifacts.map((artifact) => ({ artifact, overflowCount: 0 })),
      ...(published.length > 4
        ? [{ artifact: null, overflowCount: published.length - 3 }]
        : []),
    ];
    entries.forEach((entry, index) => {
      const tileId = entry.artifact
        ? artifactNodeId(node.id, entry.artifact.name)
        : artifactOverflowNodeId(node.id);
      const position = layoutHints[tileId] ?? {
        x: startX + index * 170,
        y: baseY + LANE.artifactOffsetY,
      };
      if (ownerParent?.startsWith("planspace:")) {
        recordChildExtent(
          ownerParent.slice("planspace:".length),
          position.x,
          position.y,
          160,
          80,
        );
      }
      rfNodes.push({
        id: tileId,
        type: "artifact",
        position,
        width: 160,
        height: 70,
        data: {
          ownerNodeId: node.id,
          artifact: entry.artifact,
          overflowCount: entry.overflowCount,
        },
        draggable: true,
        selectable: true,
        ...(ownerParent ? { parentNode: ownerParent, extent: PLANSPACE_CHILD_EXTENT } : {}),
      });
      rfEdges.push({
        id: `produces:${node.id}->${tileId}`,
        source: node.id,
        sourceHandle: "produces",
        target: tileId,
        targetHandle: "produces",
        type: "produces",
      });
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
    declaredBy: Set<string>;
    usedBy: Set<string>;
    plugId?: string | null;
    title?: string | null;
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
        if (src.kind !== "skill") existing.usedBy.add(ownerId);
        if (src.chars > existing.chars) existing.chars = src.chars;
      } else {
        ctxAgg.set(key, {
          identityKey: key,
          scope: src.scope,
          kind: src.kind,
          path: src.path,
          chars: src.chars,
          loadedBy: new Set([ownerId]),
          declaredBy: new Set(),
          usedBy: new Set(src.kind === "skill" ? [] : [ownerId]),
          plugId: src.plug_id ?? null,
        });
      }
    }
  }

  /* Library metadata enriches observed bindings but does not enumerate unbound
   * entries onto the canvas. */
  for (const principle of principles) {
    const principlePath = `${principle.path}/CONTEXT.md`;
    const key = contextIdentityKey("contextspace", "principle", principlePath);
    const existing = ctxAgg.get(key);
    if (existing) {
      if (!existing.title) existing.title = principle.title;
      if (!existing.plugId) existing.plugId = principle.id;
    }
  }

  /* skill_audit is the observed source of native skill availability/use. */
  for (const node of visibleNodes) {
    const audit = node.settings_snapshot?.skill_audit;
    if (!Array.isArray(audit)) continue;
    for (const raw of audit) {
      if (!raw || typeof raw !== "object") continue;
      const item = raw as Record<string, unknown>;
      if (item.missing === true || item.failed === true) continue;
      const id = typeof item.id === "string" ? item.id : "";
      const skill = skills.find((candidate) => candidate.id === id);
      if (!skill) continue;
      const key = contextIdentityKey(
        "contextspace",
        "skill",
        `${skill.path}/SKILL.md`,
      );
      let aggregate = ctxAgg.get(key);
      if (!aggregate) {
        aggregate = {
          identityKey: key,
          scope: "contextspace",
          kind: "skill",
          path: `${skill.path}/SKILL.md`,
          chars: 0,
          loadedBy: new Set(),
          declaredBy: new Set(),
          usedBy: new Set(),
          plugId: skill.id,
          title: skill.title,
        };
        ctxAgg.set(key, aggregate);
      }
      if (!aggregate.title) aggregate.title = skill.title;
      if (!aggregate.plugId) aggregate.plugId = skill.id;
      aggregate.loadedBy.add(node.id);
      if (item.used === true) aggregate.usedBy.add(node.id);
    }
  }

  /* Declared bindings on visible virtuals are project state even before a run
   * observes them. Resolve ids through the library solely to obtain tile
   * metadata; missing library entries cannot materialize a tile. */
  const principleById = new Map(principles.map((item) => [item.id, item]));
  const skillById = new Map(skills.map((item) => [item.id, item]));
  for (const node of visibleNodes) {
    if (node.state !== "virtual") continue;
    for (const raw of node.pending_extra_principles ?? []) {
      const id = typeof raw === "string" ? raw.trim() : "";
      if (!id) continue;
      const plugId = id.includes(".") ? id : `principles.${id}`;
      const principle = principleById.get(plugId);
      if (!principle) continue;
      const path = `${principle.path}/CONTEXT.md`;
      const key = contextIdentityKey("contextspace", "principle", path);
      let aggregate = ctxAgg.get(key);
      if (!aggregate) {
        aggregate = {
          identityKey: key,
          scope: "contextspace",
          kind: "principle",
          path,
          chars: 0,
          loadedBy: new Set(),
          declaredBy: new Set(),
          usedBy: new Set(),
          plugId,
          title: principle.title,
        };
        ctxAgg.set(key, aggregate);
      }
      aggregate.declaredBy.add(node.id);
    }
    for (const selection of node.pending_extra_skills ?? []) {
      const skill = selection?.id ? skillById.get(selection.id) : undefined;
      if (!skill) continue;
      const path = `${skill.path}/SKILL.md`;
      const key = contextIdentityKey("contextspace", "skill", path);
      let aggregate = ctxAgg.get(key);
      if (!aggregate) {
        aggregate = {
          identityKey: key,
          scope: "contextspace",
          kind: "skill",
          path,
          chars: 0,
          loadedBy: new Set(),
          declaredBy: new Set(),
          usedBy: new Set(),
          plugId: skill.id,
          title: skill.title,
        };
        ctxAgg.set(key, aggregate);
      }
      aggregate.declaredBy.add(node.id);
    }
  }

  /* Three placement regimes for ctx tiles:
   *   - Project-root scope → neutral top stripe (project-wide reference).
   *   - plugId names a known planspace → joins that lane as a child, so
   *     STATUS/PLAN/CONTEXT live visually inside the planspace they belong to.
   *   - Everything else (principle CONTEXT not bound to a planspace, …) lives in
   *     the floating "loaded context" stripe below the top one.
   * The split keeps project-wide references separate from planspace-owned
   * memory while still showing free-form loads. */
  let projectCtxCursorX = LANE.rootX + LANE.trunkGutter;
  const firstLaneId = planspaceOrder[0];
  const firstLanePosition = firstLaneId ? laneAbsPos.get(firstLaneId) : undefined;
  let firstLaneContextCursorX = LANE.planspaceLanePaddingX;
  let firstLaneContextRight = 0;
  if (firstLaneId) {
    for (const agg of ctxAgg.values()) {
      if (agg.scope === "project-root" || agg.plugId !== firstLaneId) continue;
      const stored = layoutHints[`ctx:${agg.identityKey}`];
      const positionX = stored?.x ?? firstLaneContextCursorX;
      firstLaneContextRight = Math.max(firstLaneContextRight, positionX + 160);
      firstLaneContextCursorX += LANE.planspaceCtxStep;
    }
  }
  const firstLaneContentRight = Math.max(
    firstLaneContextRight,
    firstLaneId ? (laneChildMaxX.get(firstLaneId) ?? 0) : 0,
  );
  let laneCtxCursorX = firstLanePosition
    ? firstLanePosition.x + firstLaneContentRight + LANE.planspaceLanePaddingX
    : LANE.rootX + LANE.trunkGutter;
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
    let extent: CoordinateExtent | undefined;
    if (homeLaneId) {
      const cursor =
        inLaneCtxCursor.get(homeLaneId) ?? LANE.planspaceLanePaddingX;
      position = stored ?? { x: cursor, y: LANE.planspaceLaneCtxRowY };
      inLaneCtxCursor.set(homeLaneId, cursor + LANE.planspaceCtxStep);
      parentNode = `planspace:${homeLaneId}`;
      extent = PLANSPACE_CHILD_EXTENT;
      /* Width here matches ContextNode (160 for non-project tiles). */
      recordChildExtent(homeLaneId, position.x, position.y, 160, LANE.contextHeight);
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
      width: isProject ? 220 : 160,
      height: 70,
      data: {
        identityKey: agg.identityKey,
        scope: agg.scope,
        kind: agg.kind,
        path: agg.path,
        filename: filenameOf(agg.path),
        chars: agg.chars,
        loadedByNodeIds: Array.from(agg.loadedBy),
        plugId: agg.plugId ?? null,
        title: agg.title ?? null,
        usedByNodeIds: Array.from(agg.usedBy),
      },
      draggable: true,
      ...(parentNode ? { parentNode, extent } : {}),
    });
    /* Context tiles sit above the agent row, so a load enters the tile's top
     * `loads` handle. Op tiles have only the left/right pair, so their loads
     * edges keep the default anchors — naming a handle a node does not carry
     * would make React Flow drop the edge. */
    const loadsTargetHandle = (ownerId: string): string | undefined =>
      allNodeById.get(ownerId)?.kind === "op" ? undefined : "loads";
    for (const ownerId of agg.loadedBy) {
      const used = agg.kind !== "skill" || agg.usedBy.has(ownerId);
      rfEdges.push({
        id: `ld:${ctxId}->${ownerId}`,
        source: ctxId,
        sourceHandle: "loads",
        target: ownerId,
        targetHandle: loadsTargetHandle(ownerId),
        type: "loads",
        data: { relation: used ? "used" : "available" },
      });
    }
    for (const ownerId of agg.declaredBy) {
      if (agg.loadedBy.has(ownerId)) continue;
      rfEdges.push({
        id: `ld:${ctxId}->${ownerId}`,
        source: ctxId,
        sourceHandle: "loads",
        target: ownerId,
        targetHandle: loadsTargetHandle(ownerId),
        type: "loads",
        data: { relation: "declared" },
      });
    }
  }

  /* Lane swimlanes. Constructed AFTER both the main child loop and the ctx
   * loop so the per-lane width includes the longest of (agent row, ctx row).
   * Spliced at the front because React Flow requires parents to come before
   * their children. */
  const laneNodes: RFNode[] = [];
  let nextAutoLaneY = LANE.timelineY - LANE.planspaceLaneAgentRowY;
  for (const planspaceId of planspaceOrder) {
    const maxRight =
      laneChildMaxX.get(planspaceId) ?? (LANE.planspaceLanePaddingX + LANE.agentWidth);
    const width = Math.max(
      LANE.agentWidth + LANE.planspaceLanePaddingX * 2,
      maxRight + LANE.planspaceLanePaddingX,
    );
    const maxBottom =
      laneChildMaxY.get(planspaceId) ?? (LANE.planspaceLaneAgentRowY + LANE.agentHeight);
    const height = Math.max(
      LANE.planspaceLaneHeight,
      maxBottom + LANE.planspaceLaneBottomPadding,
    );
    const hintedPos = layoutHints[`planspace:${planspaceId}`];
    const fallbackPos = laneAbsPos.get(planspaceId);
    const pos = hintedPos ?? (
      fallbackPos ? { x: fallbackPos.x, y: nextAutoLaneY } : null
    );
    if (!pos) continue;
    if (!hintedPos) {
      nextAutoLaneY += height + 40;
    } else {
      nextAutoLaneY = Math.max(nextAutoLaneY, hintedPos.y + height + 40);
    }
    const color =
      laneColors.get(planspaceId) ??
      colorForPlanspace(planspaceId, planspaceIndex, planspaceColorOverrides) ??
      PLANSPACE_PALETTE[0];
    laneNodes.push({
      id: `planspace:${planspaceId}`,
      type: "planspaceLane",
      position: pos,
      width,
      height,
      data: {
        planspaceId,
        label: labelForPlanspace(planspaceId),
        nodeCount: laneChildCount.get(planspaceId) ?? 0,
        width,
        height,
        color,
        active: planspaceId === activePlanspaceId,
        canCreateVirtual,
      },
      selectable: true,
      draggable: true,
      dragHandle: ".planspace-lane-drag-handle",
      zIndex: -20,
    });
  }
  rfNodes.splice(0, 0, ...laneNodes);

  /* Use the same child-bounds calculation as interactive drag-stop fitting.
   * The incremental extents above are useful while materializing the lane,
   * but React Flow node dimensions are the canonical geometry exposed to the
   * resize path. Keeping the final fit shared prevents upstream node updates
   * from restoring a different bottom gutter than a manual drag. */
  const fittedRfNodes = resizePlanspaceLanes(
    rfNodes,
    new Set(laneNodes.map((node) => node.id)),
    true,
    layoutHints,
  );

  return {
    rfNodes: fittedRfNodes,
    rfEdges,
    epochMembersByCommitSha,
    commitHubIdByNodeId,
  };
}

/* ───────── helpers ───────── */

export function resizePlanspaceLanes(
  nodes: RFNode[],
  laneIds: ReadonlySet<string>,
  shrinkToFit: boolean,
  layoutHints: Readonly<Record<string, { x: number; y: number }>> = {},
): RFNode[] {
  if (laneIds.size === 0) return nodes;

  const desiredByLaneId = new Map<string, { width: number; height: number }>();
  for (const laneId of laneIds) {
    desiredByLaneId.set(laneId, {
      width: LANE.agentWidth + LANE.planspaceLanePaddingX * 2,
      height: LANE.planspaceLaneHeight,
    });
  }
  for (const node of nodes) {
    if (!node.parentNode || !laneIds.has(node.parentNode)) continue;
    const desired = desiredByLaneId.get(node.parentNode);
    if (!desired) continue;
    desired.width = Math.max(
      desired.width,
      node.position.x + (node.width ?? 0) + LANE.planspaceLanePaddingX,
    );
    desired.height = Math.max(
      desired.height,
      node.position.y + (node.height ?? 0) + LANE.planspaceLaneBottomPadding,
    );
  }

  let changed = false;
  let heightChanged = false;
  const resized = nodes.map((node) => {
    if (node.type !== "planspaceLane" || !laneIds.has(node.id)) return node;
    const desired = desiredByLaneId.get(node.id);
    if (!desired) return node;
    const data = node.data as PlanspaceLaneData;
    const width = shrinkToFit
      ? desired.width
      : Math.max(node.width ?? data.width, desired.width);
    const height = shrinkToFit
      ? desired.height
      : Math.max(node.height ?? data.height, desired.height);
    if (
      width === node.width &&
      height === node.height &&
      width === data.width &&
      height === data.height
    ) {
      return node;
    }
    changed = true;
    heightChanged ||= height !== node.height || height !== data.height;
    return {
      ...node,
      width,
      height,
      data: { ...data, width, height },
    };
  });
  if (!changed) return nodes;
  if (!heightChanged) return resized;

  let nextAutoLaneY = LANE.timelineY - LANE.planspaceLaneAgentRowY;
  return resized.map((node) => {
    if (node.type !== "planspaceLane") return node;
    const height = node.height ?? (node.data as PlanspaceLaneData).height;
    if (layoutHints[node.id]) {
      nextAutoLaneY = Math.max(nextAutoLaneY, node.position.y + height + 40);
      return node;
    }
    const position = node.position.y === nextAutoLaneY
      ? node.position
      : { ...node.position, y: nextAutoLaneY };
    nextAutoLaneY += height + 40;
    return position === node.position ? node : { ...node, position };
  });
}

export function classifyPlanspaceLaneResizes(
  nodes: RFNode[],
  changes: NodeChange[],
): { growLaneIds: Set<string>; fitLaneIds: Set<string> } {
  const growLaneIds = new Set<string>();
  const fitLaneIds = new Set<string>();
  const currentById = new Map(nodes.map((node) => [node.id, node]));
  for (const change of changes) {
    if (change.type === "dimensions") {
      const parentNode = currentById.get(change.id)?.parentNode;
      if (parentNode?.startsWith("planspace:")) fitLaneIds.add(parentNode);
      continue;
    }
    if (change.type !== "position") continue;
    const parentNode = currentById.get(change.id)?.parentNode;
    if (!parentNode?.startsWith("planspace:")) continue;
    if (change.dragging === false) {
      fitLaneIds.add(parentNode);
      growLaneIds.delete(parentNode);
    } else if (change.position && !fitLaneIds.has(parentNode)) {
      growLaneIds.add(parentNode);
    }
  }
  return { growLaneIds, fitLaneIds };
}

export function contextIdentityKey(scope: string, kind: string, path: string): string {
  return `${scope}::${kind}::${path}`;
}

export function artifactNodeId(nodeId: string, name: string): string {
  return `artifact:${nodeId}:${encodeURIComponent(name)}`;
}

export function artifactOverflowNodeId(nodeId: string): string {
  return `artifact-overflow:${nodeId}`;
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
  const sourceId = findContinueSourceId(node, byId);
  if (!sourceId) return null;
  const parent = byId.get(sourceId);
  if (!parent || parent.kind === "op") return null;
  return parent;
}

function visibleScheduledDepIds(
  node: NodeInfo,
  byId: Map<string, NodeInfo>,
): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const depId of node.scheduled_deps ?? []) {
    if (seen.has(depId) || !byId.has(depId)) continue;
    seen.add(depId);
    out.push(depId);
  }
  return out;
}

function findContinueSourceId(
  node: NodeInfo,
  byId: Map<string, NodeInfo>,
): string | null {
  if (node.resume_from_node_id && byId.has(node.resume_from_node_id)) {
    return node.resume_from_node_id;
  }
  if (!node.parent_node_id || !byId.has(node.parent_node_id)) return null;
  const parent = byId.get(node.parent_node_id);
  if (!parent || parent.kind === "op" || node.kind !== "agent") return null;
  if (node.category === "review") return null;
  if ((node.scheduled_deps ?? []).length > 0) return null;
  return node.parent_node_id;
}

function virtualBranchAnchorId(
  node: NodeInfo,
  byId: Map<string, NodeInfo>,
): string | null {
  const continueSourceId = findContinueSourceId(node, byId);
  if (continueSourceId) return continueSourceId;
  for (const depId of node.scheduled_deps ?? []) {
    if (byId.has(depId)) return depId;
  }
  return null;
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
  layoutHints: Record<string, { x: number; y: number }>,
): number {
  const base = LANE.rootX + LANE.trunkGutter;
  const firstLaneId = planspaceOrder[0];
  if (!firstLaneId) return base;
  const firstLaneAbs = laneAbsPos.get(firstLaneId);
  if (!firstLaneAbs) return base;

  let laneCursor = LANE.planspaceLanePaddingX;
  let occupiedRight = LANE.planspaceLanePaddingX + LANE.agentWidth;
  for (const node of visibleNodes) {
    if (resolvePlanspaceId(node, byId) !== firstLaneId) continue;
    const geometry = renderedWorkNodeGeometry(node);
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
): { spacing: number; width: number } | null {
  if (node.kind === "op") {
    if (node.state === "done") return null;
    return { spacing: LANE.opSpacing, width: LANE.opWidth };
  }
  return { spacing: LANE.agentSpacing, width: LANE.agentWidth };
}

function isVirtualReady(
  node: NodeInfo,
  byId: Map<string, NodeInfo>,
): boolean {
  if (node.state !== "virtual" || node.obsolete_reason) return false;
  if (!(node.prompt_draft || "").trim()) return false;
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
    ? planspaceId.slice(planspaceId.lastIndexOf(".") + 1)
    : planspaceId;
  return raw
    .split(/[-_]/g)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}
