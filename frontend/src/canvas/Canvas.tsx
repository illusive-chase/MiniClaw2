import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeChange,
  type NodeMouseHandler,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "reactflow";
import "reactflow/dist/style.css";

import type { ContextBundle, NodeInfo } from "../types";
import {
  buildGraph,
  type RFNode,
  type RFEdge,
} from "./layout";
import { AgentNode } from "./nodes/AgentNode";
import { GateNode } from "./nodes/GateNode";
import { OpNode } from "./nodes/OpNode";
import { ContextNode } from "./nodes/ContextNode";
import { PhantomNode } from "./nodes/PhantomNode";
import { ProjectRootNode } from "./nodes/ProjectRootNode";
import { PlanspaceLaneNode } from "./nodes/PlanspaceLaneNode";
import {
  LoadsEdge,
  MemoryDeltaEdge,
  OpChevronEdge,
  ResumeEdge,
  ReviewsEdge,
  TimelineEdge,
  setOpChevronContext,
} from "./edges/TimelineEdge";
import { ErrorTerminalNode } from "./nodes/ErrorTerminalNode";

const NODE_TYPES = {
  agent: AgentNode,
  gate: GateNode,
  op: OpNode,
  context: ContextNode,
  phantom: PhantomNode,
  projectRoot: ProjectRootNode,
  planspaceLane: PlanspaceLaneNode,
  errorTerminal: ErrorTerminalNode,
};

const EDGE_TYPES = {
  timeline: TimelineEdge,
  resume: ResumeEdge,
  reviews: ReviewsEdge,
  loads: LoadsEdge,
  opChevron: OpChevronEdge,
  memoryDelta: MemoryDeltaEdge,
};

export type CanvasSelection =
  | { kind: "agent" | "gate" | "op"; nodeId: string }
  | {
      kind: "context";
      identityKey: string;
      path: string;
      scope: string;
      sourceKind: string;
      plugId?: string | null;
    }
  | { kind: "planspace"; planspaceId: string }
  | { kind: "projectRoot" }
  | { kind: "none" };

export type CanvasProps = {
  nodes: NodeInfo[];
  selectedNodeId: string | null;
  activeNodeId: string | null;
  projectTitle: string;
  /** focused phantom: undefined = no phantom; null = fresh start; string = resuming from */
  phantomFromNodeId: string | null | undefined;
  phantomDisabled: boolean;
  contextBundlesByNodeId: Record<string, ContextBundle | null | undefined>;
  knownPlanspaceIds: string[];
  hiddenPlanspaceIds: string[];
  activePlanspaceId: string | null;
  /** Persisted positions hydrated from the session. */
  initialLayoutHints?: Record<string, { x: number; y: number }>;
  onSelectionChange: (sel: CanvasSelection) => void;
  /** spawn the phantom to the right of a finished agent */
  onSpawnFromAgent: (nodeId: string) => void;
  /** Called debounced after drag-end with positions that changed. */
  onLayoutHintsChange?: (updates: Record<string, { x: number; y: number }>) => void;
};

export function Canvas(props: CanvasProps) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}

function CanvasInner({
  nodes,
  selectedNodeId,
  activeNodeId,
  projectTitle,
  phantomFromNodeId,
  phantomDisabled,
  contextBundlesByNodeId,
  knownPlanspaceIds,
  hiddenPlanspaceIds,
  activePlanspaceId,
  initialLayoutHints,
  onSelectionChange,
  onSpawnFromAgent,
  onLayoutHintsChange,
}: CanvasProps) {
  const layoutHintsRef = useRef<Record<string, { x: number; y: number }>>(
    sanitizeLayoutHints(initialLayoutHints),
  );
  const pendingHintsRef = useRef<Record<string, { x: number; y: number }>>({});
  const flushTimerRef = useRef<number | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);

  /* Re-hydrate when the session changes (initialLayoutHints prop swap). */
  useEffect(() => {
    layoutHintsRef.current = sanitizeLayoutHints(initialLayoutHints);
  }, [initialLayoutHints]);

  const flushPendingHints = useCallback(() => {
    if (flushTimerRef.current !== null) {
      window.clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    const pending = pendingHintsRef.current;
    if (Object.keys(pending).length === 0) return;
    pendingHintsRef.current = {};
    onLayoutHintsChange?.(pending);
  }, [onLayoutHintsChange]);

  useEffect(() => {
    return () => {
      flushPendingHints();
    };
  }, [flushPendingHints]);

  /* Op chevrons live inside edges, so React Flow's selection callback never
   * sees their clicks. We wire the click into a module-level singleton so the
   * EdgeLabelRenderer button can push selection up here. */
  useEffect(() => {
    setOpChevronContext({
      onSelectOp: (opNodeId) => onSelectionChange({ kind: "op", nodeId: opNodeId }),
    });
  }, [onSelectionChange]);

  /* Build the graph. Phantom presence is a function of phantomFromNodeId:
   *   undefined → no phantom in the graph
   *   null      → phantom in "fresh start" mode
   *   string    → phantom continuing from that node
   */
  const built = useMemo(
    () =>
      buildGraph({
        nodes,
        activeNodeId,
        projectTitle,
        phantomFromNodeId,
        phantomFreshStart: phantomFromNodeId === null,
        phantomDisabled,
        layoutHints: layoutHintsRef.current,
        contextBundlesByNodeId,
        knownPlanspaceIds,
        hiddenPlanspaceIds,
        activePlanspaceId,
      }),
    [
      nodes,
      activeNodeId,
      projectTitle,
      phantomFromNodeId,
      phantomDisabled,
      contextBundlesByNodeId,
      knownPlanspaceIds,
      hiddenPlanspaceIds,
      activePlanspaceId,
    ],
  );

  const phantomVisible = phantomFromNodeId !== undefined;

  /* React Flow controlled state. We keep an internal copy so dragging is smooth
   * while still reflecting upstream prop changes (e.g. node_updated events). */
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(
    decorateSelection(built.rfNodes, selectedNodeId, phantomVisible),
  );
  const [rfEdges, setRfEdges] = useEdgesState(
    decorateEdges(built.rfEdges, selectedNodeId, hoveredNodeId),
  );

  /* Sync upstream node changes into local state without trampling drag
   * positions. Critically, this effect must NOT depend on hover state — hover
   * does not change node identity, and forcing a node-list rewrite on every
   * mouseenter/leave makes React Flow's pointer hit-test churn enough to lose
   * its grip on which element is under the cursor, producing the cursor flicker
   * between pane (grab) and node (pointer). */
  useEffect(() => {
    setRfNodes((current) => {
      const positionById = new Map(current.map((n) => [n.id, n.position]));
      const next = built.rfNodes.map((n) => {
        const existing = positionById.get(n.id);
        // Only allocate a new object when the carried-over position actually
        // differs; stable refs let React Flow skip work and keep hit-test stable.
        if (existing && (existing.x !== n.position.x || existing.y !== n.position.y)) {
          return { ...n, position: existing };
        }
        return n;
      });
      return decorateSelection(next, selectedNodeId, phantomVisible);
    });
  }, [built.rfNodes, selectedNodeId, phantomVisible, setRfNodes]);

  /* Edges depend on hover (the `loads` lane fades in only for the hovered or
   * selected node), so they get a separate effect. */
  useEffect(() => {
    setRfEdges(decorateEdges(built.rfEdges, selectedNodeId, hoveredNodeId));
  }, [built.rfEdges, selectedNodeId, hoveredNodeId, setRfEdges]);

  /* Persist drag positions client-side so they survive node updates, and
   * debounce a push to the backend so refreshes survive too. */
  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      for (const ch of changes) {
        if (ch.type === "position" && ch.position && ch.dragging === false) {
          layoutHintsRef.current[ch.id] = ch.position;
          pendingHintsRef.current[ch.id] = ch.position;
          if (flushTimerRef.current !== null) {
            window.clearTimeout(flushTimerRef.current);
          }
          flushTimerRef.current = window.setTimeout(() => {
            flushPendingHints();
          }, 500);
        }
      }
      onNodesChange(changes);
    },
    [onNodesChange, flushPendingHints],
  );

  /* Click → selection. We translate the clicked React Flow node into the
   * parent's polymorphic CanvasSelection shape.
   *
   * We deliberately use `onNodeClick` instead of React Flow's
   * `onSelectionChange`. The latter is fired by React Flow whenever its
   * internal selection store changes — including changes WE caused by setting
   * `selected` on nodes via decorateSelection. Subscribing to it created a
   * tight feedback loop (RF.selection → App.selection → setRfNodes → RF
   * mirrors → fires onSelectionChange again), which manifested as cursor
   * flicker, fetch storms, and visible re-renders on every pane click.
   * onNodeClick only fires for genuine user clicks, breaking the loop. */
  const onNodeClick = useCallback<NodeMouseHandler>(
    (_, node) => {
      const n = node as RFNode;
      if (n.type === "agent") {
        const data = n.data as import("./layout").AgentNodeData;
        onSelectionChange({ kind: "agent", nodeId: data.node.id });
      } else if (n.type === "gate") {
        const data = n.data as import("./layout").GateNodeData;
        onSelectionChange({ kind: "gate", nodeId: data.node.id });
      } else if (n.type === "op") {
        const data = n.data as import("./layout").OpNodeData;
        onSelectionChange({ kind: "op", nodeId: data.node.id });
      } else if (n.type === "context") {
        const data = n.data as import("./layout").ContextNodeData;
        onSelectionChange({
          kind: "context",
          identityKey: data.identityKey,
          path: data.path,
          scope: data.scope,
          sourceKind: data.kind,
          plugId: data.plugId ?? null,
        });
      } else if (n.type === "projectRoot") {
        onSelectionChange({ kind: "projectRoot" });
      } else if (n.type === "planspaceLane") {
        const data = n.data as import("./layout").PlanspaceLaneData;
        onSelectionChange({ kind: "planspace", planspaceId: data.planspaceId });
      } else if (n.type === "errorTerminal") {
        const data = n.data as import("./layout").ErrorTerminalData;
        /* The terminal itself has no panel — selecting it focuses its owner. */
        onSelectionChange({
          kind: data.ownerKind === "gate" ? "gate" : "agent",
          nodeId: data.ownerNodeId,
        });
      } else if (n.type === "phantom") {
        /* Clicks inside the composer (textarea, buttons, etc.) bubble up as a
         * node click on the phantom. We intentionally don't propagate this to
         * onSelectionChange — that would dismiss the very composer the user is
         * editing. The phantom's "selected" appearance is driven directly by
         * phantomFromNodeId in decorateSelection below. */
      }
    },
    [onSelectionChange],
  );

  /* Empty-canvas tap: clear selection. The dismiss-on-deselect logic in App
   * uses this same `none` transition to dismiss the phantom composer. */
  const onPaneClick = useCallback(
    (event: React.MouseEvent) => {
      const target = event.target as HTMLElement;
      const isPane =
        target.classList.contains("react-flow__pane") ||
        target.classList.contains("react-flow__renderer");
      if (!isPane) return;
      onSelectionChange({ kind: "none" });
    },
    [onSelectionChange],
  );

  const onNodeMouseEnter = useCallback<NodeMouseHandler>((_, node) => {
    setHoveredNodeId(node.id);
  }, []);
  const onNodeMouseLeave = useCallback<NodeMouseHandler>(() => {
    setHoveredNodeId(null);
  }, []);

  /* Double-click an agent → spawn phantom from it. */
  const onNodeDoubleClick = useCallback<NodeMouseHandler>(
    (_, node) => {
      const rfNode = node as RFNode;
      if (rfNode.type === "agent") {
        const data = rfNode.data as import("./layout").AgentNodeData;
        if (data.node.kind === "agent") {
          onSpawnFromAgent(data.node.id);
        }
      }
    },
    [onSpawnFromAgent],
  );

  return (
    <div className="relative h-full w-full">
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        onNodesChange={handleNodesChange}
        onNodeClick={onNodeClick}
        onNodeMouseEnter={onNodeMouseEnter}
        onNodeMouseLeave={onNodeMouseLeave}
        onNodeDoubleClick={onNodeDoubleClick}
        onPaneClick={onPaneClick}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        defaultEdgeOptions={{
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: "rgb(var(--border-strong))",
            width: 14,
            height: 14,
          },
        }}
        defaultViewport={{ x: 0, y: 0, zoom: 0.9 }}
        minZoom={0.3}
        maxZoom={1.6}
        panOnScroll
        panOnDrag
        selectionOnDrag={false}
        selectNodesOnDrag={false}
        snapToGrid
        snapGrid={[8, 8]}
        nodesConnectable={false}
        deleteKeyCode={null}
        attributionPosition="bottom-right"
        proOptions={{ hideAttribution: true }}
        fitView
        fitViewOptions={{ padding: 0.2, includeHiddenNodes: false }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={16}
          size={1}
          color="rgb(var(--grid-line))"
        />
        <FitOnInit />
        <Controls
          className="!border !border-line !bg-surface-raised !shadow-card"
          showInteractive={false}
        />
        <ContextLaneLabel />
      </ReactFlow>
    </div>
  );
}

/* Floating labels so users know what the two top strips mean. */
function ContextLaneLabel() {
  return (
    <>
      <div className="pointer-events-none absolute left-4 top-2 z-10 inline-flex items-center gap-1.5 rounded border border-line border-dashed bg-surface-raised/85 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle backdrop-blur">
        <span className="inline-block h-1 w-1 rounded-full bg-ink-subtle" />
        Project context
      </div>
      <div className="pointer-events-none absolute left-4 top-[110px] z-10 inline-flex items-center gap-1.5 rounded border border-line bg-surface-raised/85 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle backdrop-blur">
        <span className="inline-block h-1 w-1 rounded-full bg-ink-subtle" />
        Loaded context
      </div>
    </>
  );
}

/* React Flow fitView only runs once on mount unless we re-call it. After
 * we have nodes for the first time, fit + center. */
function FitOnInit() {
  const { fitView } = useReactFlow();
  const didFit = useRef(false);
  useEffect(() => {
    if (didFit.current) return;
    const id = window.setTimeout(() => {
      fitView({ padding: 0.2, duration: 200 });
      didFit.current = true;
    }, 50);
    return () => window.clearTimeout(id);
  }, [fitView]);
  return null;
}

/**
 * Layout hints are an opaque frontend-owned map keyed by React Flow node id.
 * Keep every finite coordinate so post-migration work-node drags survive
 * refresh/reopen; this function is only a defensive clone/shape check.
 */
function sanitizeLayoutHints(
  hints: Record<string, { x: number; y: number } | null | undefined> | undefined,
): Record<string, { x: number; y: number }> {
  if (!hints) return {};
  const out: Record<string, { x: number; y: number }> = {};
  for (const [id, pos] of Object.entries(hints)) {
    if (pos && Number.isFinite(pos.x) && Number.isFinite(pos.y)) {
      out[id] = { x: pos.x, y: pos.y };
    }
  }
  return out;
}

function decorateSelection(
  nodes: RFNode[],
  selectedNodeId: string | null,
  phantomVisible: boolean,
): Node[] {
  return nodes.map((n) => {
    /* The phantom composer is the "selected" thing while it's on screen,
     * regardless of the underlying canvas selection. That way the SidePanel
     * keeps showing whatever the user was looking at while they author the
     * next run. */
    const isPhantom = n.id === "phantom:composer";
    const selected = isPhantom ? phantomVisible : n.id === selectedNodeId;
    return n.selected === selected ? n : { ...n, selected };
  });
}

function decorateEdges(
  edges: RFEdge[],
  selectedNodeId: string | null,
  hoveredNodeId: string | null,
): Edge[] {
  return edges.map((e) => {
    if (e.type === "loads") {
      const endpoint = e.source === selectedNodeId || e.target === selectedNodeId
        || e.source === hoveredNodeId || e.target === hoveredNodeId;
      return { ...e, style: { ...(e.style ?? {}), opacity: endpoint ? 0.75 : 0 } };
    }
    if (e.type === "opChevron") {
      const opId = (e.data as { op?: { id?: string } } | undefined)?.op?.id;
      if (opId && opId === selectedNodeId) {
        return {
          ...e,
          data: { ...(e.data as object), opSelected: true },
          selected: true,
        };
      }
      return {
        ...e,
        data: { ...(e.data as object), opSelected: false },
      };
    }
    if (selectedNodeId && (e.source === selectedNodeId || e.target === selectedNodeId)) {
      return { ...e, selected: true };
    }
    return e;
  });
}
