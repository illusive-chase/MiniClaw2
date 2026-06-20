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
  type Viewport,
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
import { OpNode } from "./nodes/OpNode";
import { ContextNode } from "./nodes/ContextNode";
import { PhantomNode } from "./nodes/PhantomNode";
import { ProjectRootNode } from "./nodes/ProjectRootNode";
import { PlanspaceLaneNode } from "./nodes/PlanspaceLaneNode";
import {
  DependencyEdge,
  LoadsEdge,
  OpChevronEdge,
  ResumeEdge,
  TimelineEdge,
  setOpChevronContext,
} from "./edges/TimelineEdge";
import { ErrorTerminalNode } from "./nodes/ErrorTerminalNode";

const NODE_TYPES = {
  agent: AgentNode,
  op: OpNode,
  context: ContextNode,
  phantom: PhantomNode,
  projectRoot: ProjectRootNode,
  planspaceLane: PlanspaceLaneNode,
  errorTerminal: ErrorTerminalNode,
};

const EDGE_TYPES = {
  dependency: DependencyEdge,
  timeline: TimelineEdge,
  resume: ResumeEdge,
  loads: LoadsEdge,
  opChevron: OpChevronEdge,
};

const LAYOUT_DRAG_SAVE_DEBOUNCE_MS = 500;

export type CanvasSelection =
  | { kind: "agent" | "op"; nodeId: string }
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
  canCreateVirtual: boolean;
  /** Persisted positions hydrated from the session. */
  initialLayoutHints?: Record<string, { x: number; y: number }>;
  /** Persisted viewport hydrated from the session. */
  initialLayoutViewport?: Viewport | null;
  onSelectionChange: (sel: CanvasSelection) => void;
  /** spawn the phantom to the right of a finished agent */
  onSpawnFromAgent: (nodeId: string) => void;
  /** Called after drag-end / pan / zoom with layout state that changed. */
  onLayoutHintsChange?: (
    updates: Record<string, { x: number; y: number }>,
    viewport?: Viewport | null,
  ) => void;
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
  canCreateVirtual,
  initialLayoutHints,
  initialLayoutViewport,
  onSelectionChange,
  onSpawnFromAgent,
  onLayoutHintsChange,
}: CanvasProps) {
  const layoutHintsRef = useRef<Record<string, { x: number; y: number }>>(
    sanitizeLayoutHints(initialLayoutHints),
  );
  const initialViewportRef = useRef<Viewport | null>(
    sanitizeViewport(initialLayoutViewport),
  );
  const viewportRef = useRef<Viewport | null>(initialViewportRef.current);
  const pendingHintsRef = useRef<Record<string, { x: number; y: number }>>({});
  const pendingViewportRef = useRef<Viewport | null>(null);
  const flushTimerRef = useRef<number | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const { getViewport, setViewport } = useReactFlow();
  const [layoutHydrationVersion, setLayoutHydrationVersion] = useState(0);
  const [viewportHydrationVersion, setViewportHydrationVersion] = useState(0);
  const appliedLayoutHydrationVersionRef = useRef(layoutHydrationVersion);

  /* Re-hydrate when the session changes (initialLayoutHints prop swap). A ref
   * update alone is not enough because buildGraph is memoized, and the RF sync
   * pass normally preserves current positions to protect active drags. */
  useEffect(() => {
    const next = sanitizeLayoutHints(initialLayoutHints);
    if (sameLayoutHints(layoutHintsRef.current, next)) return;
    layoutHintsRef.current = next;
    setLayoutHydrationVersion((version) => version + 1);
  }, [initialLayoutHints]);

  /* defaultViewport is only read by React Flow on mount. If a mounted canvas is
   * given hydrated session state later, apply that persisted viewport
   * explicitly. */
  useEffect(() => {
    const next = sanitizeViewport(initialLayoutViewport);
    if (sameViewportOrNull(initialViewportRef.current, next)) return;
    initialViewportRef.current = next;
    viewportRef.current = next;
    setViewportHydrationVersion((version) => version + 1);
  }, [initialLayoutViewport]);

  const flushPendingLayout = useCallback(() => {
    if (flushTimerRef.current !== null) {
      window.clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    const pending = pendingHintsRef.current;
    const pendingViewport = pendingViewportRef.current;
    if (Object.keys(pending).length === 0 && !pendingViewport) return;
    pendingHintsRef.current = {};
    pendingViewportRef.current = null;
    onLayoutHintsChange?.(pending, pendingViewport);
  }, [onLayoutHintsChange]);

  const scheduleFlushLayout = useCallback(
    (delayMs: number) => {
      if (flushTimerRef.current !== null) {
        window.clearTimeout(flushTimerRef.current);
      }
      flushTimerRef.current = window.setTimeout(() => {
        flushPendingLayout();
      }, delayMs);
    },
    [flushPendingLayout],
  );

  useEffect(() => {
    return () => {
      flushPendingLayout();
    };
  }, [flushPendingLayout]);

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
        canCreateVirtual,
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
      canCreateVirtual,
      layoutHydrationVersion,
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
    const hydrateFromLayout =
      appliedLayoutHydrationVersionRef.current !== layoutHydrationVersion;
    setRfNodes((current) => {
      const positionById = new Map(current.map((n) => [n.id, n.position]));
      const next = built.rfNodes.map((n) => {
        if (hydrateFromLayout) return n;
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
    if (hydrateFromLayout) {
      appliedLayoutHydrationVersionRef.current = layoutHydrationVersion;
    }
  }, [
    built.rfNodes,
    selectedNodeId,
    phantomVisible,
    setRfNodes,
    layoutHydrationVersion,
  ]);

  useEffect(() => {
    if (viewportHydrationVersion === 0) return;
    const next = initialViewportRef.current;
    if (!next) return;
    const id = window.setTimeout(() => {
      setViewport(next, { duration: 0 });
    }, 0);
    return () => window.clearTimeout(id);
  }, [setViewport, viewportHydrationVersion]);

  /* Edges depend on hover (the `loads` lane fades in only for the hovered or
   * selected node), so they get a separate effect. */
  useEffect(() => {
    setRfEdges(decorateEdges(built.rfEdges, selectedNodeId, hoveredNodeId));
  }, [built.rfEdges, selectedNodeId, hoveredNodeId, setRfEdges]);

  /* Persist drag positions client-side so they survive node updates, and
   * debounce a push to the backend so refreshes survive too. */
  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      let changed = false;
      for (const ch of changes) {
        if (ch.type === "position" && ch.position && ch.dragging === false) {
          layoutHintsRef.current[ch.id] = ch.position;
          pendingHintsRef.current[ch.id] = ch.position;
          changed = true;
        }
      }
      if (changed) scheduleFlushLayout(LAYOUT_DRAG_SAVE_DEBOUNCE_MS);
      onNodesChange(changes);
    },
    [onNodesChange, scheduleFlushLayout],
  );

  const onMoveEnd = useCallback(
    (event: MouseEvent | TouchEvent | null, viewport: Viewport) => {
      /* Programmatic fitView can report a move-end without a user event. Keep
       * saved viewports user-owned so auto-fit does not overwrite them. */
      if (!event) return;
      const next = sanitizeViewport(viewport);
      if (!next || sameViewport(viewportRef.current, next)) return;
      viewportRef.current = next;
      pendingViewportRef.current = next;
      scheduleFlushLayout(250);
    },
    [scheduleFlushLayout],
  );

  const persistCurrentViewport = useCallback(() => {
    window.setTimeout(() => {
      const next = sanitizeViewport(getViewport());
      if (!next || sameViewport(viewportRef.current, next)) return;
      viewportRef.current = next;
      pendingViewportRef.current = next;
      scheduleFlushLayout(0);
    }, 0);
  }, [getViewport, scheduleFlushLayout]);

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
          kind: "agent",
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
        onMoveEnd={onMoveEnd}
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
        defaultViewport={initialViewportRef.current ?? { x: 0, y: 0, zoom: 0.9 }}
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
        fitView={!initialViewportRef.current}
        fitViewOptions={{ padding: 0.2, includeHiddenNodes: false }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={16}
          size={1}
          color="rgb(var(--grid-line))"
        />
        <FitOnInit enabled={!initialViewportRef.current} />
        <Controls
          className="!border !border-line !bg-surface-raised !shadow-card"
          showInteractive={false}
          onZoomIn={persistCurrentViewport}
          onZoomOut={persistCurrentViewport}
          onFitView={persistCurrentViewport}
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
function FitOnInit({ enabled }: { enabled: boolean }) {
  const { fitView } = useReactFlow();
  const didFit = useRef(false);
  useEffect(() => {
    if (!enabled) return;
    if (didFit.current) return;
    const id = window.setTimeout(() => {
      fitView({ padding: 0.2, duration: 200 });
      didFit.current = true;
    }, 50);
    return () => window.clearTimeout(id);
  }, [enabled, fitView]);
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

function sanitizeViewport(
  viewport: Viewport | null | undefined,
): Viewport | null {
  if (
    viewport &&
    Number.isFinite(viewport.x) &&
    Number.isFinite(viewport.y) &&
    Number.isFinite(viewport.zoom) &&
    viewport.zoom > 0
  ) {
    return { x: viewport.x, y: viewport.y, zoom: viewport.zoom };
  }
  return null;
}

function sameViewport(a: Viewport | null, b: Viewport): boolean {
  if (!a) return false;
  return (
    Math.abs(a.x - b.x) < 0.01 &&
    Math.abs(a.y - b.y) < 0.01 &&
    Math.abs(a.zoom - b.zoom) < 0.0001
  );
}

function sameViewportOrNull(a: Viewport | null, b: Viewport | null): boolean {
  if (!a && !b) return true;
  if (!a || !b) return false;
  return sameViewport(a, b);
}

function sameLayoutHints(
  a: Record<string, { x: number; y: number }>,
  b: Record<string, { x: number; y: number }>,
): boolean {
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  for (const key of aKeys) {
    const left = a[key];
    const right = b[key];
    if (!right || left.x !== right.x || left.y !== right.y) return false;
  }
  return true;
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
