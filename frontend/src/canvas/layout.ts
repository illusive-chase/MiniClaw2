import type { Edge, Node } from "reactflow";
import type { ContextBundle, NodeInfo } from "../types";

/* ───────── canvas node payloads ───────── */

export type AgentNodeData = {
  node: NodeInfo;
  index: number;
  resumeParent: NodeInfo | null;
  /** true when this agent is currently streaming text in the live channel */
  isActive: boolean;
};

export type GateNodeData = AgentNodeData;

export type OpNodeData = {
  node: NodeInfo;
  parent: NodeInfo | null;
  child: NodeInfo | null;
};

export type ArtifactNodeData = {
  /** Owning agent / gate node */
  ownerNodeId: string;
  /** Kind of artifact: result.md, result.json, brief.md, review.json … */
  artifactKind: "summary" | "interface" | "review_brief" | "review_response";
  /** File label shown on the tile */
  filename: string;
  /** path relative to the project root (or absolute, as the backend stores it) */
  path: string;
  ownerState: NodeInfo["state"];
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

export type RFNodeData =
  | AgentNodeData
  | GateNodeData
  | OpNodeData
  | ArtifactNodeData
  | ContextNodeData
  | PhantomNodeData
  | ProjectRootNodeData;

export type RFNode = Node<RFNodeData>;
export type RFEdge = Edge;

/* ───────── geometry ───────── */

export const LANE = {
  rootX: 40,
  timelineY: 220,
  contextLaneY: 40,
  artifactOffsetX: 240,
  artifactOffsetY: 140,
  agentWidth: 224,
  agentSpacing: 280,
  opWidth: 96,
  opSpacing: 140,
  gateSpacing: 240,
};

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
  /** synthetic artifact node ids per agent node id */
  artifactsByOwnerId: Record<string, string[]>;
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
  const artifactsByOwnerId: Record<string, string[]> = {};
  const nodeById = new Map<string, NodeInfo>();
  for (const n of nodes) nodeById.set(n.id, n);

  /* project root anchor */
  rfNodes.push({
    id: "root",
    type: "projectRoot",
    position: layoutHints["root"] ?? { x: LANE.rootX, y: LANE.timelineY },
    data: { title: projectTitle },
    draggable: true,
    selectable: true,
  });

  /* main timeline: agents, gates, ops along x = index*spacing */
  let cursorX = LANE.rootX + 180;
  nodes.forEach((node, index) => {
    const resumeParent = findResumeParent(node, nodeById);
    const isActive = node.id === activeNodeId;
    const stored = layoutHints[node.id];

    if (node.kind === "op") {
      const parent = node.parent_node_id ? (nodeById.get(node.parent_node_id) ?? null) : null;
      const childIdx = nodes.findIndex(
        (n) => n.parent_node_id === node.id && n.kind !== "op",
      );
      const child = childIdx >= 0 ? nodes[childIdx] : null;
      const position = stored ?? { x: cursorX, y: LANE.timelineY };
      rfNodes.push({
        id: node.id,
        type: "op",
        position,
        data: { node, parent, child },
        draggable: true,
      });
      cursorX += LANE.opSpacing;
    } else if (node.kind === "gate") {
      const position = stored ?? { x: cursorX, y: LANE.timelineY };
      rfNodes.push({
        id: node.id,
        type: "gate",
        position,
        data: { node, index, resumeParent, isActive },
        draggable: true,
      });
      cursorX += LANE.gateSpacing;
    } else {
      const position = stored ?? { x: cursorX, y: LANE.timelineY };
      rfNodes.push({
        id: node.id,
        type: "agent",
        position,
        data: { node, index, resumeParent, isActive },
        draggable: true,
      });
      cursorX += LANE.agentSpacing;
    }

    /* timeline / resume edge from FS-parent */
    if (node.parent_node_id && nodeById.has(node.parent_node_id)) {
      const parentNode = nodeById.get(node.parent_node_id)!;
      const isResume = parentNode.kind !== "op" && node.kind !== "op";
      rfEdges.push({
        id: `tl:${node.parent_node_id}->${node.id}`,
        source: node.parent_node_id,
        target: node.id,
        type: isResume ? "resume" : "timeline",
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

  /* artifact nodes — one per agent with an output_path */
  nodes.forEach((node) => {
    if (node.kind === "op") return;
    if (!node.output_kind || node.output_kind === "freeform") return;
    if (!node.output_path) return;

    const artifactKind = node.output_kind === "review_brief"
      ? "review_brief"
      : node.output_kind;
    const filename = filenameOf(node.output_path);
    const artifactId = `artifact:${node.id}`;
    const sourceNode = rfNodes.find((n) => n.id === node.id);
    const baseX = sourceNode?.position.x ?? LANE.rootX;
    const baseY = sourceNode?.position.y ?? LANE.timelineY;
    const stored = layoutHints[artifactId];
    rfNodes.push({
      id: artifactId,
      type: "artifact",
      position: stored ?? {
        x: baseX + LANE.artifactOffsetX - 60,
        y: baseY + LANE.artifactOffsetY,
      },
      data: {
        ownerNodeId: node.id,
        artifactKind,
        filename,
        path: node.output_path,
        ownerState: node.state,
      },
      draggable: true,
    });
    artifactsByOwnerId[node.id] = [artifactId];
    rfEdges.push({
      id: `pr:${node.id}->${artifactId}`,
      source: node.id,
      target: artifactId,
      type: "produces",
    });

    /* If this is a review_brief, find the downstream gate node that reviews it */
    if (node.output_kind === "review_brief") {
      const downstream = nodes.find(
        (n) => n.kind === "gate" && n.parent_node_id === node.id,
      );
      if (downstream) {
        rfEdges.push({
          id: `rv:${artifactId}->${downstream.id}`,
          source: artifactId,
          target: downstream.id,
          type: "reviews",
        });
      }
    }
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

  let ctxCursorX = LANE.rootX + 180;
  for (const agg of ctxAgg.values()) {
    const ctxId = `ctx:${agg.identityKey}`;
    const stored = layoutHints[ctxId];
    rfNodes.push({
      id: ctxId,
      type: "context",
      position: stored ?? { x: ctxCursorX, y: LANE.contextLaneY },
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
    ctxCursorX += 180;
    for (const ownerId of agg.loadedBy) {
      rfEdges.push({
        id: `ld:${ctxId}->${ownerId}`,
        source: ctxId,
        target: ownerId,
        type: "loads",
      });
    }
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

  return { rfNodes, rfEdges, artifactsByOwnerId };
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
