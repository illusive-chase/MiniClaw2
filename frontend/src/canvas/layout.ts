import type { CoordinateExtent, Edge, Node, NodeChange } from "reactflow";
import type {
  ArtifactRef,
  CommitDescriptor,
  ContextBundle,
  NodeInfo,
  SessionHost,
  TemplateInstanceRecord,
} from "../types";

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
  /** Template arguments this node's prompt references. Only an embedded
   * template session supplies these; ordinary projects leave it undefined and
   * render exactly as before. */
  templateArguments?: string[];
};

/** One declared input port of the template being edited in an embedded session.
 *
 * A port is not an agent: it carries no state, never reaches the scheduler, and
 * exists only so the template's signature is something the author can see and
 * connect. It lives on the planspace manifest rather than in `scheduled_deps`,
 * because the backend resolves every dep through `load_node` and an `in:<port>`
 * literal resolves to nothing. */
export type TemplatePortRecord = {
  name: string;
  description?: string;
  /** Node ids whose prompts consume this port. */
  consumers?: string[];
};

export type TemplatePortNodeData = {
  name: string;
  description: string;
  consumerIds: string[];
  /** No node consumes this port, so it would be dropped on save. */
  unreferenced: boolean;
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
  /** skills auto-attached with this skill, folded into this tile (skill tiles only) */
  attachedSkills?: AttachedSkillDisplay[];
};

export type AttachedSkillDisplay = {
  id: string;
  title: string;
  reason: "dependency" | "package";
  /** run ids where this folded skill was actually invoked */
  usedByNodeIds: string[];
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

export type CommitColumnHeaderData = {
  host: SessionHost | null;
  head: string;
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
  auto: boolean;
  canActivate: boolean;
  canCreateVirtual: boolean;
};

/** One `key=value` pair from the instance record, for the group header. */
export type TemplateArgumentDisplay = {
  name: string;
  value: string;
};

/** Rolled-up member states. `hasError` drives the error accent independently
 * of the counts so a single failure is never averaged away. */
export type TemplateInstanceProgress = {
  total: number;
  done: number;
  running: number;
  hasError: boolean;
};

/** The frame drawn around an expanded instance. Purely decorative: it is a
 * lane sibling of its members, not their React Flow parent, so member drag,
 * extent and lane fitting keep working exactly as they do outside a group. */
export type TemplateGroupData = {
  instanceId: string;
  label: string;
  argumentSummary: TemplateArgumentDisplay[];
  progress: TemplateInstanceProgress;
  width: number;
  height: number;
  color: PlanspaceColor | null;
};

/** The collapsed stand-in for a whole instance. */
export type TemplateInstanceBoxData = {
  instanceId: string;
  label: string;
  argumentSummary: TemplateArgumentDisplay[];
  progress: TemplateInstanceProgress;
  /** Members with no downstream inside the instance — the implicit outputs a
   * new downstream node attaches to. */
  sinkNodeIds: string[];
  memberNodeIds: string[];
  color: PlanspaceColor | null;
  canCreateVirtual: boolean;
};

export type RFNodeData =
  | AgentNodeData
  | OpNodeData
  | ContextNodeData
  | CommitNodeData
  | CommitColumnHeaderData
  | PlanspaceLaneData
  | TemplateGroupData
  | TemplateInstanceBoxData
  | TemplatePortNodeData
  | ErrorTerminalData
  | ArtifactNodeData;

export type RFNode = Node<RFNodeData>;
export type RFEdge = Edge;

/* ───────── geometry ───────── */

const AGENT_NODE_HEIGHT = 86;

/* An argument chip row adds a line to the card. The rfNode height has to grow
 * with it: the real height is CSS-driven but layout (lane fitting, sibling
 * stacking, group bounds) reads the number declared here, and letting the two
 * diverge shows up as overlapping tiles and short lanes. Raising
 * AGENT_NODE_HEIGHT itself instead would loosen spacing in every ordinary
 * project, which is why this is a per-node addend. */
const AGENT_ARG_CHIP_ROW_HEIGHT = 22;
const AGENT_ARG_CHIPS_PER_ROW = 2;

function agentNodeHeight(argumentCount: number): number {
  const rows = Math.ceil(Math.max(0, argumentCount) / AGENT_ARG_CHIPS_PER_ROW);
  return AGENT_NODE_HEIGHT + rows * AGENT_ARG_CHIP_ROW_HEIGHT;
}

export const LANE = {
  rootX: 40,
  timelineY: 220,
  trunkX: 40,
  trunkStartY: 80,
  trunkStep: 112,
  trunkColumnStep: 196,
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
  /* Input ports of an embedded template session. They sit in their own row
   * above the agent row so a port never competes with a node for a slot. */
  templatePortWidth: 168,
  templatePortHeight: 52,
  templatePortSpacing: 196,
  templatePortRowY: 148,
  templateSessionAgentRowY: 224,
  /* Lane is laid out vertically as: header band → ctx row → agent row → bottom pad.
   * Y values below are RELATIVE positions inside the lane (origin = lane top-left). */
  planspaceLaneSpacing: 360,
  planspaceLanePaddingX: 40,
  planspaceLanePaddingY: 40,
  planspaceLaneCtxRowY: 52,
  planspaceLaneAgentRowY: 156,
  planspaceLaneGap: 40,
  /* The child bounds add their own top position and bottom padding. */
  planspaceLaneMinHeight: AGENT_NODE_HEIGHT,
  /* Horizontal step between ctx tiles inside a lane (tile width ~160 + gap). */
  planspaceCtxStep: 180,
  /* Template instance groups. Members are laid out in a row inside the frame,
   * which is inset from them by `templateGroupPadding` on every side plus a
   * header band on top. A collapsed instance renders as one agent-sized tile. */
  templateGroupPadding: 20,
  templateGroupHeaderHeight: 34,
  templateGroupGap: 32,
  templateBoxWidth: 224,
  templateBoxHeight: 96,
};

const PLANSPACE_CHILD_EXTENT: CoordinateExtent = [
  [LANE.planspaceLanePaddingX, LANE.planspaceLanePaddingY],
  [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY],
];

/** Lane backgrounds sit at -20 and ordinary tiles at the React Flow default.
 * A group frame belongs between them: behind its own members, in front of the
 * lane it lives in. */
export const TEMPLATE_GROUP_NODE_Z_INDEX = -10;

export function templateGroupNodeId(instanceId: string): string {
  return `tplgroup:${instanceId}`;
}

export function templateInstanceBoxNodeId(instanceId: string): string {
  return `tplbox:${instanceId}`;
}

/** Render id prefix for an embedded session's input ports. */
const TEMPLATE_PORT_ID_PREFIX = "tplport:";

/** Render id for one input port of an embedded template session.
 *
 * Prefixed so it can never collide with a real node id, which is what lets
 * ports share the canvas's single id space without reaching the scheduler. */
export function templatePortNodeId(portName: string): string {
  return `${TEMPLATE_PORT_ID_PREFIX}${portName}`;
}

/** Inverse of {@link templatePortNodeId}; null for any non-port render id. */
export function templatePortNameFromNodeId(nodeId: string): string | null {
  if (!nodeId.startsWith(TEMPLATE_PORT_ID_PREFIX)) return null;
  return nodeId.slice(TEMPLATE_PORT_ID_PREFIX.length) || null;
}

export function snapPlanspaceChildPosition(
  flowPosition: { x: number; y: number },
  lanePosition: { x: number; y: number },
  gridSize = 8,
): { x: number; y: number } {
  const minX = PLANSPACE_CHILD_EXTENT[0][0];
  const minY = PLANSPACE_CHILD_EXTENT[0][1];
  return {
    x: Math.max(minX, Math.round((flowPosition.x - lanePosition.x) / gridSize) * gridSize),
    y: Math.max(minY, Math.round((flowPosition.y - lanePosition.y) / gridSize) * gridSize),
  };
}

/**
 * Lane-relative placement for a node the user just asked for — the lane header
 * "+" button and the Git "Review" button — as opposed to one the layout is
 * merely re-deriving.
 *
 * The automatic default (`nextLanePosition`) appends along a horizontal cursor
 * on the agent row, which walks a busy lane far off to the right while leaving
 * the new tile pinned to the top row. A node created by an explicit click
 * should instead land where the user is already looking: under the work that
 * lane is currently doing.
 *
 * `x` follows `anchorNodeId` — the lane's most recently *active* node, chosen by
 * the caller from node timestamps rather than from geometry, so the new tile
 * lines up with the branch actually in play. `y` clears every existing child of
 * the lane, so the tile cannot land on top of one regardless of how the lane
 * has been rearranged by hand.
 *
 * Returns null when the lane has no children yet; the caller then has nothing
 * to append to and the ordinary default is already correct. Also returns null
 * when `forNodeId` is already a child of the lane: a node that is on the canvas
 * has a position the user may have chosen, and moving it would yank a tile they
 * are already looking at.
 */
export function appendBelowLanePosition(
  laneNodeId: string,
  nodes: readonly RFNode[],
  anchorNodeId: string | null,
  forNodeId?: string,
): { x: number; y: number } | null {
  let maxBottom: number | null = null;
  let anchorX: number | null = null;
  for (const node of nodes) {
    if (node.parentNode !== laneNodeId) continue;
    if (forNodeId !== undefined && node.id === forNodeId) return null;
    /* Derived frames enclose their members rather than occupying the lane
     * themselves, so counting them would leave a redundant gap. */
    if (node.type === "templateGroup") continue;
    const height = node.height ?? AGENT_NODE_HEIGHT;
    const bottom = node.position.y + height;
    if (maxBottom === null || bottom > maxBottom) maxBottom = bottom;
    if (node.id === anchorNodeId) anchorX = node.position.x;
  }
  if (maxBottom === null) return null;
  return {
    x: Math.max(LANE.planspaceLanePaddingX, anchorX ?? LANE.planspaceLanePaddingX),
    y: Math.max(
      LANE.planspaceLaneAgentRowY,
      maxBottom + LANE.siblingYStep - AGENT_NODE_HEIGHT,
    ),
  };
}

/** Pick the newest candidate that has live geometry in this lane. The mapper
 * translates durable IDs hidden by a collapsed template to the box rendered
 * in their place. */
export function resolveRenderedLaneAnchorId(
  laneNodeId: string,
  nodes: readonly RFNode[],
  candidateNodeIds: readonly string[],
  renderIdFor: (nodeId: string) => string = (nodeId) => nodeId,
): string | null {
  const renderedChildren = new Set(
    nodes
      .filter((node) => node.parentNode === laneNodeId)
      .map((node) => node.id),
  );
  for (const candidateId of candidateNodeIds) {
    const renderedId = renderIdFor(candidateId);
    if (renderedChildren.has(renderedId)) return renderedId;
  }
  return null;
}

/** Vertical span of one lane in flow coordinates, plus the flow Y a jump should
 * land the viewport on at each end. `topY` is the lane's own top edge — the
 * header band, which is the landmark the user recognizes — while `bottomY`
 * clears the lowest child so the last tile is fully above the bottom edge. */
export type LaneVerticalSpan = {
  laneNodeId: string;
  topY: number;
  bottomY: number;
};

/** Vertical span of the lane a jump should target, or null when the lane is
 * absent from the live canvas (an empty planspace has no lane node yet, and a
 * hidden one is filtered out before layout).
 *
 * Height comes from the live React Flow node when it has been measured and
 * falls back to the built `data` dimensions, matching `resizePlanspaceLanes`:
 * a lane whose children were just dragged reports its new height through the
 * node before the next rebuild refreshes `data`.
 */
export function resolveLaneVerticalSpan(
  nodes: readonly RFNode[],
  planspaceId: string | null,
): LaneVerticalSpan | null {
  if (!planspaceId) return null;
  const laneNodeId = `planspace:${planspaceId}`;
  const lane = nodes.find((node) => node.id === laneNodeId);
  if (!lane || lane.type !== "planspaceLane") return null;
  const data = lane.data as PlanspaceLaneData;
  const height = lane.height ?? data.height;
  return {
    laneNodeId,
    topY: lane.position.y,
    bottomY: lane.position.y + height,
  };
}

/**
 * Whether a lane is long enough for the top/bottom jump affordance to be worth
 * offering, expressed as "the lane does not fit in `viewportHeight` screens".
 *
 * Measured in screen pixels rather than flow units so the test matches what the
 * user can actually see: zooming out until the whole lane fits should retire the
 * buttons, and the same lane at 1.6x zoom should keep them.
 *
 * `screens` is the multiple of the viewport height a lane must exceed. Below
 * that, scrolling is short enough that the buttons would be noise.
 */
export function laneNeedsVerticalJump(
  span: LaneVerticalSpan | null,
  viewportHeight: number,
  zoom: number,
  screens = 2,
): boolean {
  if (!span) return false;
  if (!Number.isFinite(viewportHeight) || viewportHeight <= 0) return false;
  if (!Number.isFinite(zoom) || zoom <= 0) return false;
  return (span.bottomY - span.topY) * zoom > viewportHeight * screens;
}

/**
 * Which end of the lane a jump would actually move the viewport to, given where
 * the viewport currently sits — so the button that would do nothing can be
 * disabled instead of silently no-opping.
 *
 * `viewportFlowTop`/`viewportFlowBottom` are the visible band in flow
 * coordinates. The tolerance absorbs the sub-pixel drift left by an animated
 * pan, which would otherwise leave a button enabled immediately after using it.
 */
export function availableLaneJumps(
  span: LaneVerticalSpan | null,
  viewportFlowTop: number,
  viewportFlowBottom: number,
  toleranceFlowPx = 8,
): { canJumpToTop: boolean; canJumpToBottom: boolean } {
  if (!span) return { canJumpToTop: false, canJumpToBottom: false };
  return {
    canJumpToTop: viewportFlowTop > span.topY + toleranceFlowPx,
    canJumpToBottom: viewportFlowBottom < span.bottomY - toleranceFlowPx,
  };
}

/**
 * Left offset, in screen pixels, that clears React Flow's `Controls` column so
 * the lane-jump buttons sit beside the zoom / fit-view stack instead of over it.
 *
 * Measured from the live element rather than hardcoded: the column's width comes
 * from React Flow's own stylesheet (`.react-flow__panel` margin plus a
 * content-box button), so an upstream restyle would silently move it out from
 * under a constant. Falls back to the current upstream geometry — 15px margin +
 * 26px button + 2px of our border ring — when the element is not mounted yet.
 *
 * `offsetLeft` is measured against the nearest positioned ancestor. That is the
 * `.react-flow` root, which upstream styles `position: relative` at 100% of our
 * own wrapper with no offset of its own, so the value is directly comparable to
 * the wrapper-relative `left` these buttons are placed with.
 */
export function resolveLaneJumpLeftOffset(
  controls: Pick<HTMLElement, "offsetLeft" | "offsetWidth"> | null,
  gapPx = 8,
): number {
  const fallback = 43;
  if (!controls) return fallback + gapPx;
  const right = controls.offsetLeft + controls.offsetWidth;
  /* A hidden or not-yet-laid-out panel measures zero; the constant is closer to
   * the truth than pinning the buttons to the canvas edge. */
  if (!Number.isFinite(right) || right <= 0) return fallback + gapPx;
  return right + gapPx;
}

/**
 * Resolve a node position while synchronizing a rebuilt graph into React Flow.
 * Explicit UI placement (for example a double-click creation target) must win
 * even when the node already appeared at an automatic runtime position.
 */
export function resolveSyncedNodePosition(
  builtPosition: { x: number; y: number },
  runtimePosition: { x: number; y: number } | undefined,
  preserveRuntimePosition: boolean,
  explicitPosition?: { x: number; y: number },
): { x: number; y: number } {
  if (explicitPosition) return explicitPosition;
  if (preserveRuntimePosition && runtimePosition) return runtimePosition;
  return builtPosition;
}

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

/* ───────── skill attachment folding ───────── */

/** One skill entry from a node's `skill_audit` or `pending_extra_skills`. */
export type SkillAttachmentEntry = {
  id: string;
  name?: string;
  used?: boolean;
  auto_attached?: boolean;
  required_by?: string;
  attachment_reason?: string;
};

export type SkillAttachmentSplit = {
  /** Explicitly selected skills — the only ones that render as canvas tiles. */
  roots: SkillAttachmentEntry[];
  /** Auto-attached skills keyed by the explicit root that pulled them in. */
  attachedByRoot: Map<string, SkillAttachmentEntry[]>;
};

/**
 * Fold auto-attached skills (sibling dependencies and skill-pack members)
 * under the explicitly selected skill that pulled them in. `required_by`
 * names the immediate requirer, which may itself be auto-attached, so chains
 * are walked to the explicit root. Unresolvable chains fail open as roots so
 * a malformed record never hides a skill.
 */
export function splitSkillAttachments(
  entries: SkillAttachmentEntry[],
): SkillAttachmentSplit {
  const byId = new Map(entries.map((entry) => [entry.id, entry]));
  const roots: SkillAttachmentEntry[] = [];
  const attachedByRoot = new Map<string, SkillAttachmentEntry[]>();
  for (const entry of entries) {
    if (entry.auto_attached !== true) {
      roots.push(entry);
      continue;
    }
    const seen = new Set([entry.id]);
    let cursor: SkillAttachmentEntry | undefined = entry;
    let rootId: string | null = null;
    while (cursor) {
      const parentId = cursor.required_by;
      if (!parentId || seen.has(parentId)) break;
      seen.add(parentId);
      cursor = byId.get(parentId);
      if (cursor && cursor.auto_attached !== true) {
        rootId = cursor.id;
        break;
      }
    }
    if (rootId === null) {
      roots.push(entry);
      continue;
    }
    const list = attachedByRoot.get(rootId);
    if (list) list.push(entry);
    else attachedByRoot.set(rootId, [entry]);
  }
  return { roots, attachedByRoot };
}

/** Coerce a raw `skill_audit` array into attachment entries, dropping
 * missing/failed materializations exactly like tile aggregation does. */
export function coerceSkillAuditEntries(audit: unknown): SkillAttachmentEntry[] {
  if (!Array.isArray(audit)) return [];
  const entries: SkillAttachmentEntry[] = [];
  for (const raw of audit) {
    if (!raw || typeof raw !== "object") continue;
    const item = raw as Record<string, unknown>;
    if (item.missing === true || item.failed === true) continue;
    if (typeof item.id !== "string") continue;
    entries.push({
      id: item.id,
      name: typeof item.name === "string" ? item.name : undefined,
      used: item.used === true,
      auto_attached: item.auto_attached === true,
      required_by:
        typeof item.required_by === "string" ? item.required_by : undefined,
      attachment_reason:
        typeof item.attachment_reason === "string"
          ? item.attachment_reason
          : undefined,
    });
  }
  return entries;
}

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
  /** planspaces in the currently resolved binding */
  activatablePlanspaceIds: string[];
  /** planspaces hidden by per-project view state */
  hiddenPlanspaceIds: string[];
  /** active write target */
  activePlanspaceId: string | null;
  /** planspaces configured to auto-promote when active */
  autoPlanspaceIds: string[];
  /** true when the active lane's virtual create button should be enabled */
  canCreateVirtual: boolean;
  /** Stamped template instances for the visible planspaces. Supplies the group
   * header's template name and argument values; nodes carry only the id. */
  templateInstances?: TemplateInstanceRecord[];
  /** Instances currently drawn as a single collapsed box. View state only —
   * it never reaches the backend and never affects scheduling. */
  collapsedTemplateInstanceIds?: string[];
  /** User-wide principles, used to resolve bound ids to paths and titles. */
  principles?: PrincipleEnumeration[];
  /** Native Agent Skills enumerated from GET /skills. */
  skills?: SkillEnumeration[];
  /** Input ports of the template being edited, when this project is an
   * embedded template session. Absent for every ordinary project, which is
   * what keeps this addition invisible there. */
  templatePorts?: TemplatePortRecord[];
  /** Template argument names per node id, for the prompt-parameter chips. */
  templateArgumentsByNodeId?: Record<string, string[]>;
  gitCommits?: CommitDescriptor[];
  gitHead?: string | null;
  gitDirtyCount?: number;
  gitHosts?: SessionHost[];
};

export type BuildGraphResult = {
  rfNodes: RFNode[];
  rfEdges: RFEdge[];
  epochMembersByCommitSha: Record<string, string[]>;
  commitHubIdByNodeId: Record<string, string>;
  /** Per instance: members, sinks and lane, for both the collapse toggle and
   * the sink-expanding dependency default. Present whether or not the instance
   * is currently collapsed. */
  templateInstances: Record<string, TemplateInstanceCluster>;
};

/** A template instance as the canvas understands it: which visible nodes it
 * owns, which of them are its outputs, and where it lives. */
export type TemplateInstanceCluster = {
  instanceId: string;
  planspaceId: string | null;
  memberNodeIds: string[];
  /** Members no other member depends on — the implicit outputs (§4.3). */
  sinkNodeIds: string[];
  collapsed: boolean;
};

export type CommitPositionTransfer = {
  fromId: "commit:ghost";
  toId: string;
  position: { x: number; y: number };
  resetGhostPosition: { x: number; y: number } | null;
};

function defaultCommitPosition(index: number, column = 0): { x: number; y: number } {
  return {
    x: LANE.trunkX + column * LANE.trunkColumnStep,
    y: LANE.trunkStartY + index * LANE.trunkStep,
  };
}

function trunkSlotKey(position: { x: number; y: number }): string {
  return `${position.x}:${position.y}`;
}

/** Step straight down until the trunk slot is free. Fallback placement only —
 * an explicit hint is honoured verbatim even where it overlaps. */
function firstUnoccupiedSlot(
  position: { x: number; y: number },
  occupied: ReadonlySet<string>,
): { x: number; y: number } {
  let y = position.y;
  while (occupied.has(trunkSlotKey({ x: position.x, y }))) y += LANE.trunkStep;
  return { x: position.x, y };
}

/** Placement state threaded through the single-pass commit loop. `resolved`,
 * `occupied` and `previousByColumn` are grown as commits are placed; the pass
 * relies on the backend emitting parents before children. */
type CommitPlacementContext = {
  layoutHints: Readonly<Record<string, { x: number; y: number }>>;
  columnBySha: ReadonlyMap<string, number>;
  resolved: Map<string, { x: number; y: number }>;
  occupied: Set<string>;
  previousByColumn: Map<number, CommitDescriptor>;
};

/**
 * Where a commit sits relative to the commits already placed: `y` below the
 * lowest resolved parent, `x` inherited only from a parent sharing its column.
 * Null when nothing upstream is placed, leaving the absolute grid as the only
 * sensible answer.
 *
 * The axes resolve independently on purpose. A merge whose lowest parent lives
 * in another column still has to clear that parent vertically, but belongs in
 * its own column horizontally.
 */
function resolveCommitAnchor(
  commit: CommitDescriptor,
  column: number,
  ctx: CommitPlacementContext,
): { x: number | null; y: number } | null {
  /* `parent_shas` carries three distinct cases: a real parent list, an
   * explicit root (empty), and "the backend did not say" (null) — the last
   * falls back to the column's previous commit, which is also where the trunk
   * edge below is drawn from. */
  const parents = commit.parent_shas;
  const previous = ctx.previousByColumn.get(column);
  const anchorShas = parents ?? (previous ? [previous.sha] : []);
  let y: number | null = null;
  let x: number | null = null;
  for (const sha of anchorShas) {
    const position = ctx.resolved.get(sha);
    if (!position) continue;
    y = y === null ? position.y : Math.max(y, position.y);
    if (ctx.columnBySha.get(sha) === column) x = position.x;
  }
  return y === null ? null : { x, y: y + LANE.trunkStep };
}

/**
 * Resolve a commit hub's position: a saved hint wins, otherwise the commit
 * lands directly below the commit it descends from.
 *
 * Anchoring to the parent's *resolved* position rather than to an absolute
 * grid row is what keeps a newly appearing commit under its predecessor after
 * the trunk has been dragged. This is independent of who created the commit,
 * so it covers UI commits, agent commits and pulls alike.
 */
function commitLayoutPosition(
  commit: CommitDescriptor,
  index: number,
  column: number,
  ctx: CommitPlacementContext,
): { x: number; y: number } {
  for (const sha of [commit.sha, ...commit.aliases]) {
    const position = ctx.layoutHints[`commit:${sha}`];
    if (position) return position;
  }
  const anchor = resolveCommitAnchor(commit, column, ctx);
  const base = anchor
    ? { x: anchor.x ?? LANE.trunkX + column * LANE.trunkColumnStep, y: anchor.y }
    : defaultCommitPosition(index, column);
  return firstUnoccupiedSlot(base, ctx.occupied);
}

function firstUnoccupiedTrunkPositionBelowHead(
  nodes: readonly RFNode[],
): { x: number; y: number } | null {
  const headNode = nodes.find(
    (node) => node.type === "commit" && (node.data as CommitNodeData).head,
  );
  if (!headNode) return null;
  const occupiedRows = new Set(
    nodes
      .filter(
        (node) =>
          node.type === "commit" &&
          node.id !== "commit:ghost" &&
          ((node.data as CommitNodeData).commit.column ?? 0) === 0,
      )
      .map((node) => node.position.y),
  );
  let y = headNode.position.y + LANE.trunkStep;
  while (occupiedRows.has(y)) y += LANE.trunkStep;
  return { x: headNode.position.x, y };
}

export function resolveCommitPositionTransfer(
  currentNodes: readonly RFNode[],
  nextNodes: readonly RFNode[],
  committedHead: string | null,
  retainedGhostPosition: { x: number; y: number } | null = null,
): CommitPositionTransfer | null {
  if (!committedHead) return null;
  const ghost = currentNodes.find((node) => node.id === "commit:ghost");
  const committedNodeId = `commit:${committedHead}`;
  const targetAlreadyRendered = currentNodes.some(
    (node) => node.id === committedNodeId,
  );
  const ghostPosition = targetAlreadyRendered
    ? retainedGhostPosition ?? ghost?.position
    : ghost?.position ?? retainedGhostPosition;
  if (!ghostPosition) return null;
  const committedNode = nextNodes.find(
    (node) => node.type === "commit" && node.id === committedNodeId,
  );
  if (!committedNode) return null;
  const nextCommitNodes = nextNodes.filter((node) => node.type === "commit");
  const nextGhostIndex = nextCommitNodes.findIndex((node) => node.id === "commit:ghost");
  return {
    fromId: "commit:ghost",
    toId: committedNode.id,
    position: { x: ghostPosition.x, y: ghostPosition.y },
    resetGhostPosition:
      nextGhostIndex >= 0
        ? {
            x: ghostPosition.x,
            y: ghostPosition.y + LANE.trunkStep,
          }
        : null,
  };
}

/**
 * Group visible nodes by `template_instance_id` and resolve each instance's
 * sinks. Runs before placement because clustering is anchor-relative and the
 * placement pass is single-pass and order-dependent: a member may appear in
 * the node list before the member it depends on.
 *
 * An instance is only clustered when all its visible members share one
 * planspace. Members split across lanes (or with no lane) are left to the
 * ordinary per-node placement — a frame cannot span two lanes, and degrading
 * to the old layout is better than dropping the nodes.
 */
export function clusterTemplateInstances(
  visibleNodes: readonly NodeInfo[],
  byId: Map<string, NodeInfo>,
  collapsedInstanceIds: readonly string[] = [],
): Map<string, TemplateInstanceCluster> {
  const collapsed = new Set(collapsedInstanceIds);
  const membersByInstance = new Map<string, NodeInfo[]>();
  for (const node of visibleNodes) {
    const instanceId = node.template_instance_id;
    if (!instanceId) continue;
    const members = membersByInstance.get(instanceId);
    if (members) members.push(node);
    else membersByInstance.set(instanceId, [node]);
  }

  const out = new Map<string, TemplateInstanceCluster>();
  for (const [instanceId, members] of membersByInstance) {
    const planspaceIds = new Set(
      members.map((member) => resolvePlanspaceId(member, byId)),
    );
    if (planspaceIds.size !== 1) continue;
    const planspaceId = members[0] ? resolvePlanspaceId(members[0], byId) : null;
    if (!planspaceId) continue;
    const memberIds = new Set(members.map((member) => member.id));
    /* A sink has no downstream INSIDE the instance. Downstream outside the
     * instance is exactly what makes it an output worth attaching to. */
    const hasInternalDownstream = new Set<string>();
    for (const member of members) {
      for (const depId of internalPredecessorIds(member, byId, memberIds)) {
        hasInternalDownstream.add(depId);
      }
    }
    out.set(instanceId, {
      instanceId,
      planspaceId,
      memberNodeIds: members.map((member) => member.id),
      sinkNodeIds: members
        .map((member) => member.id)
        .filter((id) => !hasInternalDownstream.has(id)),
      collapsed: collapsed.has(instanceId),
    });
  }
  return out;
}

/** Predecessors of `node` that are themselves members of the same instance. */
function internalPredecessorIds(
  node: NodeInfo,
  byId: Map<string, NodeInfo>,
  memberIds: ReadonlySet<string>,
): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const candidates = [
    ...(node.scheduled_deps ?? []),
    findContinueSourceId(node, byId),
  ];
  for (const id of candidates) {
    if (!id || seen.has(id) || !memberIds.has(id) || id === node.id) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

/** Roll up member states for the group header and the collapsed box. */
export function summarizeInstanceProgress(
  memberNodeIds: readonly string[],
  byId: Map<string, NodeInfo>,
): TemplateInstanceProgress {
  let done = 0;
  let running = 0;
  let hasError = false;
  for (const id of memberNodeIds) {
    const member = byId.get(id);
    if (!member) continue;
    if (member.state === "done") done += 1;
    else if (member.state === "running") running += 1;
    else if (member.state === "error") hasError = true;
  }
  return { total: memberNodeIds.length, done, running, hasError };
}

/** Argument values for the header, in declaration order, longest values cut. */
export function summarizeInstanceArguments(
  record: TemplateInstanceRecord | undefined,
  limit = 3,
): TemplateArgumentDisplay[] {
  if (!record) return [];
  return Object.entries(record.arguments ?? {})
    .slice(0, limit)
    .map(([name, value]) => ({ name, value }));
}

export function resolveGitChangesAppearancePosition(
  currentNodes: readonly RFNode[],
  nextNodes: readonly RFNode[],
): { x: number; y: number } | null {
  if (currentNodes.some((node) => node.id === "commit:ghost")) return null;
  if (!nextNodes.some((node) => node.id === "commit:ghost")) return null;
  return firstUnoccupiedTrunkPositionBelowHead(nextNodes);
}

/**
 * Where the uncommitted-changes ghost moves when a newly appearing commit
 * claims its row, keeping the trunk in reading order: commits above, pending
 * changes last.
 *
 * Returns null unless a commit that was *not* previously rendered now sits
 * exactly on the ghost's current position. An overlap already on screen —
 * including one the user made by dragging the ghost onto a hub — is left
 * alone, and a ghost that is only now appearing belongs to
 * `resolveGitChangesAppearancePosition` instead.
 *
 * This covers the case the position transfer cannot: an agent commits while
 * the working tree is still dirty, so the ghost survives the commit rather
 * than becoming the new hub.
 */
export function resolveDisplacedGhostPosition(
  currentNodes: readonly RFNode[],
  nextNodes: readonly RFNode[],
): { x: number; y: number } | null {
  const ghost = currentNodes.find((node) => node.id === "commit:ghost");
  if (!ghost) return null;
  if (!nextNodes.some((node) => node.id === "commit:ghost")) return null;
  const hubs = nextNodes.filter(
    (node) => node.type === "commit" && node.id !== "commit:ghost",
  );
  const rendered = new Set(currentNodes.map((node) => node.id));
  const claimed = hubs.some(
    (hub) =>
      !rendered.has(hub.id) &&
      hub.position.x === ghost.position.x &&
      hub.position.y === ghost.position.y,
  );
  if (!claimed) return null;
  return firstUnoccupiedSlot(
    ghost.position,
    new Set(hubs.map((hub) => trunkSlotKey(hub.position))),
  );
}

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
    activatablePlanspaceIds,
    hiddenPlanspaceIds,
    activePlanspaceId,
    autoPlanspaceIds,
    canCreateVirtual,
    templateInstances: templateInstanceRecords = [],
    collapsedTemplateInstanceIds = [],
    principles = [],
    skills = [],
    templatePorts = [],
    templateArgumentsByNodeId = {},
    gitCommits = [],
    gitHead = null,
    gitDirtyCount = 0,
    gitHosts = [],
  } = args;

  const rfNodes: RFNode[] = [];
  const rfEdges: RFEdge[] = [];
  const hiddenPlanspaces = new Set(hiddenPlanspaceIds);
  const allNodeById = new Map<string, NodeInfo>();
  for (const n of nodes) allNodeById.set(n.id, n);
  const laneVisibleNodes = nodes.filter((node) => {
    if (node.kind === "op") return false;
    const planspaceId = resolvePlanspaceId(node, allNodeById);
    return !planspaceId || !hiddenPlanspaces.has(planspaceId);
  });
  /* Instance clustering is resolved against every lane-visible node, so a
   * collapsed instance still knows all its members and sinks. Only the
   * placement pass below skips the members a collapsed box stands in for. */
  const instanceClusters = clusterTemplateInstances(
    laneVisibleNodes,
    (() => {
      const byId = new Map<string, NodeInfo>();
      for (const n of laneVisibleNodes) byId.set(n.id, n);
      return byId;
    })(),
    collapsedTemplateInstanceIds,
  );
  const instanceRecordById = new Map(
    templateInstanceRecords.map((record) => [record.instance_id, record]),
  );
  const collapsedMemberIds = new Set<string>();
  const clusterByMemberId = new Map<string, TemplateInstanceCluster>();
  for (const cluster of instanceClusters.values()) {
    for (const memberId of cluster.memberNodeIds) {
      clusterByMemberId.set(memberId, cluster);
      if (cluster.collapsed) collapsedMemberIds.add(memberId);
    }
  }
  const visibleNodes = laneVisibleNodes.filter(
    (node) => !collapsedMemberIds.has(node.id),
  );
  const visibleNodeIds = new Set(visibleNodes.map((node) => node.id));
  /* Keyed on every lane-visible node, including members a collapsed box hides.
   * Collapse is a rendering choice: dependency resolution, promote-readiness
   * and tail detection must keep seeing the real DAG. Endpoints that land on a
   * hidden member are redirected to its box by `renderIdFor` at edge time. */
  const nodeById = new Map<string, NodeInfo>();
  for (const n of laneVisibleNodes) nodeById.set(n.id, n);
  const renderIdFor = (nodeId: string): string => {
    const cluster = clusterByMemberId.get(nodeId);
    return cluster?.collapsed ? templateInstanceBoxNodeId(cluster.instanceId) : nodeId;
  };
  const planspaceOrder: string[] = [];
  for (const id of knownPlanspaceIds) {
    if (!id || hiddenPlanspaces.has(id) || planspaceOrder.includes(id)) continue;
    planspaceOrder.push(id);
  }
  /* Lane order comes from every lane-visible node so a lane whose only nodes
   * are inside a collapsed instance keeps its swimlane. */
  for (const id of collectPlanspaceOrder(laneVisibleNodes, allNodeById)) {
    if (planspaceOrder.includes(id)) continue;
    planspaceOrder.push(id);
  }
  const planspaceIndex = new Map(planspaceOrder.map((id, index) => [id, index]));
  const commitColumnOffset = Math.max(
    0,
    ...gitCommits.map((commit) => commit.column ?? 0),
  ) * LANE.trunkColumnStep;

  /* Lane absolute positions: deterministic from index, overridable by a saved
   * hint keyed `planspace:<id>`. Children inside the lane get parent-relative
   * positions, so the lane's own abs position must be resolved BEFORE we lay
   * out the children — otherwise a hinted lane drag would shift everything. */
  const laneAbsPos = new Map<string, { x: number; y: number }>();
  for (const id of planspaceOrder) {
    const idx = planspaceIndex.get(id) ?? 0;
    const defaultPos = {
      x: LANE.rootX + LANE.trunkGutter + commitColumnOffset,
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
  const nodeRenderedHeights = new Map<string, number>();
  const branchSiblingCounts = new Map<string, number>();
  const hasTemplatePortRow = (laneId: string): boolean =>
    templatePorts.length > 0 && laneId === activePlanspaceId;
  const agentRowY = (laneId: string): number =>
    hasTemplatePortRow(laneId)
      ? LANE.templateSessionAgentRowY
      : LANE.planspaceLaneAgentRowY;
  const tallestAgentHeight = Math.max(
    AGENT_NODE_HEIGHT,
    ...Object.values(templateArgumentsByNodeId).map((names) =>
      agentNodeHeight(names.length),
    ),
  );
  const branchStep = Math.max(LANE.siblingYStep, tallestAgentHeight + 20);

  const shaSet = new Set(gitCommits.map((commit) => commit.sha));
  const columnIndexes = new Map<string, number>();
  const columnCounts = new Map<number, number>();
  const commitRows = new Map<string, number>();
  const nextRowByColumn = new Map<number, number>();
  for (const commit of gitCommits) {
    const column = commit.column ?? 0;
    const columnIndex = columnCounts.get(column) ?? 0;
    columnIndexes.set(commit.sha, columnIndex);
    columnCounts.set(column, columnIndex + 1);
    const parentRow = Math.max(
      -1,
      ...(commit.parent_shas ?? []).map((sha) => commitRows.get(sha) ?? -1),
    );
    const row = Math.max(nextRowByColumn.get(column) ?? 0, parentRow + 1);
    commitRows.set(commit.sha, row);
    nextRowByColumn.set(column, row + 1);
  }
  const previousByColumn = new Map<number, CommitDescriptor>();
  const commitColumnBySha = new Map(
    gitCommits.map((commit) => [commit.sha, commit.column ?? 0] as const),
  );
  const commitPlacement: CommitPlacementContext = {
    layoutHints,
    columnBySha: commitColumnBySha,
    resolved: new Map(),
    occupied: new Set(),
    previousByColumn,
  };
  gitCommits.forEach((commit) => {
    const column = commit.column ?? 0;
    const position = commitLayoutPosition(
      commit,
      commitRows.get(commit.sha) ?? 0,
      column,
      commitPlacement,
    );
    commitPlacement.resolved.set(commit.sha, position);
    commitPlacement.occupied.add(trunkSlotKey(position));
    rfNodes.push({
      id: `commit:${commit.sha}`,
      type: "commit",
      position,
      width: 76,
      height: 76,
      data: {
        commit,
        head: commit.sha === gitHead,
        externalCountBefore:
          (columnIndexes.get(commit.sha) ?? 0) === 0
            ? commit.external_count_before
            : 0,
      },
      draggable: true,
      selectable: true,
    });
    const parents = commit.parent_shas?.filter((sha) => shaSet.has(sha));
    if (parents && parents.length > 0) {
      for (const parent of parents) {
        rfEdges.push({
          id: `commit-trunk:${parent}:${commit.sha}`,
          source: `commit:${parent}`,
          target: `commit:${commit.sha}`,
          type: "commitTrunk",
          data: { externalCount: commit.external_count_before },
        });
      }
    } else if (commit.parent_shas == null) {
      const previous = previousByColumn.get(column);
      if (previous) {
        rfEdges.push({
          id: `commit-trunk:${previous.sha}:${commit.sha}`,
          source: `commit:${previous.sha}`,
          target: `commit:${commit.sha}`,
          type: "commitTrunk",
          data: { externalCount: commit.external_count_before },
        });
      }
    }
    previousByColumn.set(column, commit);
  });
  const hostsById = new Map(gitHosts.map((host) => [host.mid, host]));
  const renderedColumns = new Set<number>();
  for (const commit of gitCommits) {
    const column = commit.column ?? 0;
    if (column <= 0 || renderedColumns.has(column)) continue;
    renderedColumns.add(column);
    const hostId = commit.host_ids?.[0];
    const host = hostId ? hostsById.get(hostId) ?? null : null;
    rfNodes.push({
      id: `commit-column:${column}`,
      type: "commitColumnHeader",
      position: {
        x: LANE.trunkX + column * LANE.trunkColumnStep - 44,
        y: LANE.trunkStartY - 64,
      },
      width: 164,
      height: 48,
      data: { host, head: host?.head ?? commit.sha },
      draggable: false,
      selectable: false,
    });
  }
  if (gitDirtyCount > 0) {
    const ghostId = "commit:ghost";
    const nextTrunkRow = nextRowByColumn.get(0) ?? 0;
    const headIndex = gitHead
      ? gitCommits.findIndex((commit) => commit.sha === gitHead)
      : -1;
    const headNode = headIndex >= 0
      ? rfNodes.find((node) => node.id === `commit:${gitHead}`)
      : undefined;
    const fallbackPosition = headNode
      ? firstUnoccupiedTrunkPositionBelowHead(rfNodes) ??
        defaultCommitPosition(nextTrunkRow)
      : defaultCommitPosition(nextTrunkRow);
    const position = layoutHints[ghostId] ?? fallbackPosition;
    rfNodes.push({ id: ghostId, type: "commit", position, width: 76, height: 76, data: { commit: { sha: "ghost", live: false, message: "Uncommitted changes", external_count_before: 0, aliases: [], column: 0 }, head: false, ghost: true, dirtyCount: gitDirtyCount }, draggable: true, selectable: true });
    const previous = gitHead
      ? gitCommits.find((commit) => commit.sha === gitHead)
      : [...gitCommits].reverse().find((commit) => (commit.column ?? 0) === 0);
    if (previous) {
      rfEdges.push({ id: `commit-trunk:${previous.sha}:ghost`, source: `commit:${previous.sha}`, target: ghostId, type: "commitTrunk" });
    }
  }

  const planspaceColorOverrides = collectPlanspaceColorOverrides(
    contextBundlesByNodeId,
  );

  /* Mark agent nodes that no other work node depends on or continues from. The
   * "+" hover affordance only appears on these tail tiles. Scanned over every
   * lane-visible node so a node feeding a collapsed instance is not mistaken
   * for a tail just because its consumer is hidden. */
  const hasDescendantById = new Set<string>();
  for (const candidate of laneVisibleNodes) {
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
    /* Lane-visible rather than placed: a collapsed instance still occupies its
     * lane, so a free top-level tile must clear it too. */
    laneVisibleNodes,
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
    const prevBottom = laneChildMaxY.get(laneId) ?? agentRowY(laneId) + LANE.agentHeight;
    if (bottom > prevBottom) laneChildMaxY.set(laneId, bottom);
    laneChildCount.set(laneId, (laneChildCount.get(laneId) ?? 0) + 1);
  };

  /* Each expanded instance takes one contiguous block of the lane's agent row.
   * The origin is claimed from the lane cursor the first time any member is
   * placed, then every later member of that instance is positioned relative to
   * it — so members cluster regardless of their order in the node list, and no
   * unrelated node can land between them. The block reserves room for all
   * members up front, which is what keeps the cursor monotonic and preserves
   * append-don't-reflow for everything placed afterwards. */
  const instanceOrigins = new Map<string, { x: number; y: number }>();
  const instanceMemberSlots = new Map<string, Map<string, number>>();
  const instanceOriginFor = (
    cluster: TemplateInstanceCluster,
    laneId: string,
  ): { x: number; y: number } => {
    const existing = instanceOrigins.get(cluster.instanceId);
    if (existing) return existing;
    const memberCount = Math.max(1, cluster.memberNodeIds.length);
    const blockWidth =
      memberCount * LANE.agentWidth +
      (memberCount - 1) * (LANE.agentSpacing - LANE.agentWidth);
    /* Reserve the frame's own inset so the border never overlaps a neighbour. */
    const origin = nextLanePosition(
      laneId,
      blockWidth + LANE.templateGroupPadding * 2 + LANE.templateGroupGap,
      undefined,
      agentRowY(laneId),
    );
    const shifted = { x: origin.x + LANE.templateGroupPadding, y: origin.y };
    instanceOrigins.set(cluster.instanceId, shifted);
    instanceMemberSlots.set(
      cluster.instanceId,
      new Map(cluster.memberNodeIds.map((id, index) => [id, index])),
    );
    return shifted;
  };
  /* A collapsed instance takes one tile-sized slot, claimed at the position of
   * its FIRST member in node order — so collapsing does not move the instance
   * to the end of the lane, and expanding it again puts it back where it was.
   * Filled during the placement pass below, consumed when the box is built. */
  const collapsedBoxPositions = new Map<string, { x: number; y: number }>();

  laneVisibleNodes.forEach((node) => {
    /* Members of a collapsed instance render as one box instead of tiles. The
     * first one reserves the box's lane slot; the rest are simply skipped. */
    const collapsedCluster = collapsedMemberIds.has(node.id)
      ? clusterByMemberId.get(node.id)
      : undefined;
    if (collapsedCluster) {
      const laneId = resolvePlanspaceId(node, allNodeById);
      if (laneId && !collapsedBoxPositions.has(collapsedCluster.instanceId)) {
        const boxId = templateInstanceBoxNodeId(collapsedCluster.instanceId);
        collapsedBoxPositions.set(
          collapsedCluster.instanceId,
          nextLanePosition(
            laneId,
            LANE.agentSpacing,
            layoutHints[boxId],
            agentRowY(laneId),
          ),
        );
      }
      return;
    }
    const resumeParent = findResumeParent(node, nodeById);
    const isActive = activeNodeIds.includes(node.id);
    const stored = layoutHints[node.id];
    const planspaceId = resolvePlanspaceId(node, allNodeById);
    const planspaceColor = colorForPlanspace(
      planspaceId,
      planspaceIndex,
      planspaceColorOverrides,
    );
    const templateArguments = templateArgumentsByNodeId[node.id];
    const hasArgChips = (templateArguments?.length ?? 0) > 0;
    const renderedAgentHeight = agentNodeHeight(templateArguments?.length ?? 0);
    const agentExtentHeight = Math.max(LANE.agentHeight, renderedAgentHeight);
    nodeRenderedHeights.set(node.id, renderedAgentHeight);
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
    /* Cluster a member into its instance's reserved block. Placed before
     * `placeInLane` in the chain so grouped members never consume a lane
     * cursor slot of their own, and after the `stored` check inside so a
     * manual drag still wins. */
    const placeInInstanceBlock = (): { x: number; y: number } | null => {
      if (!planspaceId) return null;
      const cluster = clusterByMemberId.get(node.id);
      if (!cluster || cluster.collapsed) return null;
      if (stored) {
        /* Claim the block anyway: the frame must still enclose this member,
         * and skipping it would let the next instance reuse the same slot. */
        instanceOriginFor(cluster, planspaceId);
        recordChildExtent(
          planspaceId,
          stored.x,
          stored.y,
          LANE.agentWidth,
          agentExtentHeight,
        );
        nodeRelativePositions.set(node.id, stored);
        return stored;
      }
      const origin = instanceOriginFor(cluster, planspaceId);
      const slot = instanceMemberSlots.get(cluster.instanceId)?.get(node.id) ?? 0;
      const position = {
        x: origin.x + slot * LANE.agentSpacing,
        y: origin.y,
      };
      recordChildExtent(
        planspaceId,
        position.x,
        position.y,
        LANE.agentWidth,
        agentExtentHeight,
      );
      nodeRelativePositions.set(node.id, position);
      return position;
    };
    /* Stack a node directly beneath the node it continues from: a dependency
     * parent, a resumed session, or the failed node it reruns. One shared
     * sibling counter across all three is what keeps them from landing on each
     * other — a failed node can have both a dependent virtual and a rerun
     * queued beneath it, and separate counters would give both the same slot. */
    const placeBelowAnchorInLane = (
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
          agentExtentHeight,
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
        y: anchorPosition.y + branchStep * (siblingIndex + 1),
      };
      recordChildExtent(
        planspaceId,
        position.x,
        position.y,
        LANE.agentWidth,
        agentExtentHeight,
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
        ? placeInLane(LANE.opSpacing, LANE.opWidth, LANE.opHeight, agentRowY(planspaceId))
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
      /* A rerun anchors on the node it replaces whatever state it has reached.
       * Unlike a planned virtual, it is created already runnable and an auto
       * lane promotes it to `queued` immediately, so gating on "virtual" would
       * drop the anchor for exactly the reruns that carry no dependency. */
      const rerunAnchorId = rerunSourceId(node, nodeById);
      const branchAnchorId =
        rerunAnchorId ??
        (node.state === "virtual" ? virtualBranchAnchorId(node, nodeById) : null);
      const position = planspaceId
        ? (
            /* Ahead of the anchored branch: a stamped member's scheduled_deps
             * point at its siblings, which would otherwise stack the instance
             * vertically instead of clustering it. Returns null for every
             * non-member, so ordinary layout is untouched. */
            placeInInstanceBlock() ??
            placeBelowAnchorInLane(branchAnchorId) ??
            placeInLane(
              LANE.agentSpacing,
              LANE.agentWidth,
              agentExtentHeight,
              agentRowY(planspaceId),
            )
          )
        : placeFree(LANE.agentSpacing);
      rfNodes.push({
        id: node.id,
        type: "agent",
        position,
        width: LANE.agentWidth,
        /* Kept in step with the chip row the card actually renders — layout
         * reads this number, not the CSS box. */
        height: renderedAgentHeight,
        data: {
          node,
          resumeParent,
          isActive,
          planspaceColor,
          isLastInLane,
          readyToPromote: isVirtualReady(node, nodeById),
          canCreateVirtual,
          ...(hasArgChips ? { templateArguments } : {}),
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
  for (const node of laneVisibleNodes) {
    if (node.kind === "op") continue;
    const continueSourceId = findContinueSourceId(node, nodeById);
    if (continueSourceId) continueSourceByNodeId.set(node.id, continueSourceId);
  }

  /* Both endpoints are mapped through `renderIdFor`, so a dependency crossing
   * the boundary of a collapsed instance lands on its box: inbound edges are
   * the instance's input bindings, outbound edges are downstream consumers of
   * its sinks. Edges fully inside one collapsed instance become self-loops and
   * are dropped. Iterating every lane-visible node (not just the placed ones)
   * is what lets a hidden member's own upstream edge still reach the box.
   * `pushRenderedEdge` de-duplicates because several member edges can collapse
   * onto the same pair. */
  const emittedEdgeIds = new Set<string>();
  const pushRenderedEdge = (edge: RFEdge): void => {
    const source = renderIdFor(edge.source);
    const target = renderIdFor(edge.target);
    if (source === target) return;
    const id = `${edge.id.slice(0, edge.id.indexOf(":") + 1)}${source}->${target}`;
    if (emittedEdgeIds.has(id)) return;
    emittedEdgeIds.add(id);
    rfEdges.push({ ...edge, id, source, target });
  };

  for (const node of laneVisibleNodes) {
    if (node.kind === "op") continue;
    const visibleDeps = visibleScheduledDepIds(node, nodeById);
    const continueSourceId = continueSourceByNodeId.get(node.id);
    for (const depId of visibleDeps) {
      pushRenderedEdge({
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
  for (const node of laneVisibleNodes) {
    if (node.kind === "op") continue;
    const sourceId = continueSourceByNodeId.get(node.id);
    if (!sourceId) continue;
    pushRenderedEdge({
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
    /** skills folded into this tile because they were auto-attached with it */
    attachedSkills?: Map<
      string,
      { title: string; reason: "dependency" | "package"; usedBy: Set<string> }
    >;
  };
  const ctxAgg = new Map<string, CtxAggregate>();
  for (const [ownerId, bundle] of Object.entries(contextBundlesByNodeId)) {
    const owner = allNodeById.get(ownerId);
    if (owner && !visibleNodeIds.has(ownerId)) continue;
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

  /* Only explicitly selected skills materialize tiles; skills the backend
   * auto-attached with them (sibling dependencies, skill-pack members) fold
   * into the root skill's tile so one selection never fans out into a wall
   * of dependency nodes. */
  const skillById = new Map(skills.map((item) => [item.id, item]));
  const ensureSkillAggregate = (skillId: string): CtxAggregate | null => {
    const skill = skillById.get(skillId);
    if (!skill) return null;
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
    if (!aggregate.title) aggregate.title = skill.title;
    if (!aggregate.plugId) aggregate.plugId = skill.id;
    return aggregate;
  };
  const recordAttachedSkill = (
    aggregate: CtxAggregate,
    entry: SkillAttachmentEntry,
    usedByNodeId: string | null,
  ): void => {
    const attached = (aggregate.attachedSkills ??= new Map());
    let info = attached.get(entry.id);
    if (!info) {
      info = {
        title:
          skillById.get(entry.id)?.title ??
          entry.name ??
          entry.id.replace(/^skills\./, ""),
        reason: entry.attachment_reason === "package" ? "package" : "dependency",
        usedBy: new Set<string>(),
      };
      attached.set(entry.id, info);
    }
    if (usedByNodeId) info.usedBy.add(usedByNodeId);
  };

  /* skill_audit is the observed source of native skill availability/use. */
  for (const node of visibleNodes) {
    const { roots, attachedByRoot } = splitSkillAttachments(
      coerceSkillAuditEntries(node.settings_snapshot?.skill_audit),
    );
    for (const entry of roots) {
      const aggregate = ensureSkillAggregate(entry.id);
      if (!aggregate) continue;
      aggregate.loadedBy.add(node.id);
      if (entry.used === true) aggregate.usedBy.add(node.id);
      for (const dep of attachedByRoot.get(entry.id) ?? []) {
        recordAttachedSkill(aggregate, dep, dep.used === true ? node.id : null);
      }
    }
    /* A root missing from the library cannot materialize a tile, so its
     * attachments fail open onto their own tiles instead of vanishing. */
    for (const [rootId, deps] of attachedByRoot) {
      if (skillById.has(rootId)) continue;
      for (const dep of deps) {
        const aggregate = ensureSkillAggregate(dep.id);
        if (!aggregate) continue;
        aggregate.loadedBy.add(node.id);
        if (dep.used === true) aggregate.usedBy.add(node.id);
      }
    }
  }

  /* Declared bindings on visible virtuals are project state even before a run
   * observes them. Resolve ids through the library solely to obtain tile
   * metadata; missing library entries cannot materialize a tile. */
  const principleById = new Map(principles.map((item) => [item.id, item]));
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
    const selections: SkillAttachmentEntry[] = (node.pending_extra_skills ?? [])
      .filter((selection) => typeof selection?.id === "string")
      .map((selection) => ({
        id: selection.id,
        auto_attached: selection.auto_attached === true,
        required_by: selection.required_by,
        attachment_reason: selection.attachment_reason,
      }));
    const { roots, attachedByRoot } = splitSkillAttachments(selections);
    for (const entry of roots) {
      const aggregate = ensureSkillAggregate(entry.id);
      if (!aggregate) continue;
      aggregate.declaredBy.add(node.id);
      for (const dep of attachedByRoot.get(entry.id) ?? []) {
        recordAttachedSkill(aggregate, dep, null);
      }
    }
    for (const [rootId, deps] of attachedByRoot) {
      if (skillById.has(rootId)) continue;
      for (const dep of deps) {
        const aggregate = ensureSkillAggregate(dep.id);
        if (aggregate) aggregate.declaredBy.add(node.id);
      }
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
    const attachedSkills =
      agg.attachedSkills && agg.attachedSkills.size > 0
        ? Array.from(agg.attachedSkills.entries())
            .map(([id, info]) => ({
              id,
              title: info.title,
              reason: info.reason,
              usedByNodeIds: Array.from(info.usedBy),
            }))
            .sort((a, b) => a.title.localeCompare(b.title))
        : undefined;
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
        ...(attachedSkills ? { attachedSkills } : {}),
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
    /* A folded dependency being invoked counts as the root tile being used —
     * the tile stands in for the whole attached group. */
    const attachedUsedBy = (ownerId: string): boolean =>
      agg.attachedSkills
        ? Array.from(agg.attachedSkills.values()).some((info) =>
            info.usedBy.has(ownerId),
          )
        : false;
    for (const ownerId of agg.loadedBy) {
      const used =
        agg.kind !== "skill" ||
        agg.usedBy.has(ownerId) ||
        attachedUsedBy(ownerId);
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

  /* Template instance visuals. Built AFTER every member has a position so an
   * expanded frame can be fitted to the members' actual bounds — including
   * members the user has dragged out of the reserved block. Both the frame and
   * the collapsed box feed `recordChildExtent`, so the lane grows to contain
   * them rather than clipping them. */
  const templateGroupNodes: RFNode[] = [];
  const templateBoxNodes: RFNode[] = [];
  for (const cluster of instanceClusters.values()) {
    const { instanceId, planspaceId } = cluster;
    if (!planspaceId) continue;
    const record = instanceRecordById.get(instanceId);
    const label = record?.template_name ?? record?.template_slug ?? "Template";
    const argumentSummary = summarizeInstanceArguments(record);
    const progress = summarizeInstanceProgress(cluster.memberNodeIds, nodeById);
    const color =
      laneColors.get(planspaceId) ??
      colorForPlanspace(planspaceId, planspaceIndex, planspaceColorOverrides);

    if (cluster.collapsed) {
      const boxId = templateInstanceBoxNodeId(instanceId);
      /* The slot was reserved in node order during the placement pass, so the
       * box sits where its members were rather than at the end of the lane. */
      const position =
        collapsedBoxPositions.get(instanceId) ??
        nextLanePosition(
          planspaceId,
          LANE.agentSpacing,
          layoutHints[boxId],
          agentRowY(planspaceId),
        );
      recordChildExtent(
        planspaceId,
        position.x,
        position.y,
        LANE.templateBoxWidth,
        LANE.templateBoxHeight,
      );
      templateBoxNodes.push({
        id: boxId,
        type: "templateInstanceBox",
        position,
        width: LANE.templateBoxWidth,
        height: LANE.templateBoxHeight,
        data: {
          instanceId,
          label,
          argumentSummary,
          progress,
          sinkNodeIds: cluster.sinkNodeIds,
          memberNodeIds: cluster.memberNodeIds,
          color,
          canCreateVirtual,
        },
        draggable: true,
        selectable: true,
        parentNode: `planspace:${planspaceId}`,
        extent: PLANSPACE_CHILD_EXTENT,
      });
      continue;
    }

    const memberBounds = cluster.memberNodeIds
      .map((id) => nodeRelativePositions.get(id))
      .filter((position): position is { x: number; y: number } => !!position);
    if (memberBounds.length === 0) continue;
    const minX = Math.min(...memberBounds.map((position) => position.x));
    const minY = Math.min(...memberBounds.map((position) => position.y));
    const maxX = Math.max(
      ...memberBounds.map((position) => position.x + LANE.agentWidth),
    );
    const maxY = Math.max(
      ...cluster.memberNodeIds.flatMap((id) => {
        const position = nodeRelativePositions.get(id);
        return position
          ? [position.y + (nodeRenderedHeights.get(id) ?? AGENT_NODE_HEIGHT)]
          : [];
      }),
    );
    /* The frame is inset from its members and clamped to the lane's padding so
     * a member dragged to the lane edge cannot push the border out of bounds. */
    const frameX = Math.max(
      LANE.planspaceLanePaddingX,
      minX - LANE.templateGroupPadding,
    );
    const frameY = Math.max(
      LANE.planspaceLanePaddingY,
      minY - LANE.templateGroupPadding - LANE.templateGroupHeaderHeight,
    );
    const width = maxX + LANE.templateGroupPadding - frameX;
    const height = maxY + LANE.templateGroupPadding - frameY;
    recordChildExtent(planspaceId, frameX, frameY, width, height);
    templateGroupNodes.push({
      id: templateGroupNodeId(instanceId),
      type: "templateGroup",
      position: { x: frameX, y: frameY },
      width,
      height,
      data: {
        instanceId,
        label,
        argumentSummary,
        progress,
        width,
        height,
        color,
      },
      /* Not draggable: the frame is derived from its members' bounds, so a drag
       * would be silently discarded on the next rebuild. Members move; the
       * frame follows. Pointer events are enabled only on the header band, so
       * clicks and marquee selection reach the members underneath. */
      draggable: false,
      selectable: true,
      parentNode: `planspace:${planspaceId}`,
      extent: PLANSPACE_CHILD_EXTENT,
      style: { pointerEvents: "none" },
      zIndex: TEMPLATE_GROUP_NODE_Z_INDEX,
    });
  }

  /* Input ports of an embedded template session. Emitted into the active lane
   * only, and only when the caller supplies ports at all — an ordinary project
   * passes none, so every rfNode and rfEdge below is skipped and the output is
   * byte-identical to what it was before this existed.
   *
   * The port→node edge is built from the manifest's consumer lists, not from
   * `scheduled_deps`: the backend cannot store an `in:<port>` literal there
   * (it resolves every dep through `load_node`), so the manifest is the only
   * place the edge exists. */
  const portLaneId = activePlanspaceId;
  if (templatePorts.length > 0 && portLaneId && planspaceOrder.includes(portLaneId)) {
    let portCursorX = LANE.planspaceLanePaddingX;
    for (const port of templatePorts) {
      if (!port || typeof port.name !== "string" || !port.name) continue;
      const portNodeId = templatePortNodeId(port.name);
      const stored = layoutHints[portNodeId];
      const position = stored ?? { x: portCursorX, y: LANE.templatePortRowY };
      portCursorX = Math.max(
        portCursorX,
        position.x + LANE.templatePortSpacing,
      );
      /* Only consumers that are actually on the canvas: a port pointing at a
       * deleted node must render as unreferenced rather than sprout a dangling
       * edge. */
      const consumerIds = (port.consumers ?? []).filter((id) =>
        nodeById.has(id),
      );
      rfNodes.push({
        id: portNodeId,
        type: "templatePort",
        position,
        width: LANE.templatePortWidth,
        height: LANE.templatePortHeight,
        data: {
          name: port.name,
          description: port.description ?? "",
          consumerIds,
          unreferenced: consumerIds.length === 0,
        },
        draggable: true,
        parentNode: `planspace:${portLaneId}`,
        extent: PLANSPACE_CHILD_EXTENT,
      });
      recordChildExtent(
        portLaneId,
        position.x,
        position.y,
        LANE.templatePortWidth,
        LANE.templatePortHeight,
      );
      for (const consumerId of consumerIds) {
        pushRenderedEdge({
          id: `port:${portNodeId}->${consumerId}`,
          source: portNodeId,
          target: consumerId,
          type: "dependency",
          data: {
            childState: nodeById.get(consumerId)?.state,
            overlapsContinue: false,
          },
        });
      }
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
      laneChildMaxY.get(planspaceId) ?? (agentRowY(planspaceId) + LANE.agentHeight);
    const height = Math.max(
      LANE.planspaceLaneMinHeight,
      maxBottom + LANE.planspaceLanePaddingY,
    );
    const hintedPos = layoutHints[`planspace:${planspaceId}`];
    const fallbackPos = laneAbsPos.get(planspaceId);
    const pos = hintedPos ?? (
      fallbackPos ? { x: fallbackPos.x, y: nextAutoLaneY } : null
    );
    if (!pos) continue;
    if (!hintedPos) {
      nextAutoLaneY += height + LANE.planspaceLaneGap;
    } else {
      nextAutoLaneY = Math.max(
        nextAutoLaneY,
        hintedPos.y + height + LANE.planspaceLaneGap,
      );
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
        auto: autoPlanspaceIds.includes(planspaceId),
        canActivate: activatablePlanspaceIds.includes(planspaceId),
        canCreateVirtual,
      },
      selectable: true,
      draggable: true,
      dragHandle: ".planspace-lane-drag-handle",
      style: { pointerEvents: "none" },
      zIndex: -20,
    });
  }
  /* Collapsed boxes are ordinary lane children and can go anywhere after their
   * lane. Group frames must precede the members they visually enclose only for
   * paint order, which `zIndex` already settles — but both are lane children,
   * so they follow the lane nodes spliced in below. */
  rfNodes.push(...templateBoxNodes);
  rfNodes.splice(0, 0, ...laneNodes, ...templateGroupNodes);

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
    templateInstances: Object.fromEntries(instanceClusters),
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
      height: LANE.planspaceLaneMinHeight,
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
      node.position.y + (node.height ?? 0) + LANE.planspaceLanePaddingY,
    );
  }

  let dimensionsChanged = false;
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
    dimensionsChanged = true;
    return {
      ...node,
      width,
      height,
      data: { ...data, width, height },
    };
  });

  /* A lane hint is user-owned absolute placement. Without a hint, Y is
   * derived from the preceding lane geometry and must be normalized even when
   * every lane already has the desired dimensions. */
  let nextAutoLaneY = LANE.timelineY - LANE.planspaceLaneAgentRowY;
  let positionsChanged = false;
  const positioned = resized.map((node) => {
    if (node.type !== "planspaceLane") return node;
    const height = node.height ?? (node.data as PlanspaceLaneData).height;
    if (layoutHints[node.id]) {
      nextAutoLaneY = Math.max(
        nextAutoLaneY,
        node.position.y + height + LANE.planspaceLaneGap,
      );
      return node;
    }
    const position = node.position.y === nextAutoLaneY
      ? node.position
      : { ...node.position, y: nextAutoLaneY };
    nextAutoLaneY += height + LANE.planspaceLaneGap;
    positionsChanged ||= position !== node.position;
    return position === node.position ? node : { ...node, position };
  });
  return dimensionsChanged || positionsChanged ? positioned : nodes;
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
  // Planned/promoted nodes declare graph relationships through scheduled_deps.
  // Do not fabricate a parent edge while their dependency snapshot is changing.
  if (node.proposed_by) return null;
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

function rerunSourceId(
  node: NodeInfo,
  byId: Map<string, NodeInfo>,
): string | null {
  const prefix = "rerun:";
  if (!node.proposed_by?.startsWith(prefix)) return null;
  const sourceId = node.proposed_by.slice(prefix.length);
  return byId.has(sourceId) ? sourceId : null;
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
