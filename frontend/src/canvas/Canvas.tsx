import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlowProvider,
  useOnSelectionChange,
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

const DEFAULT_VIEWPORT: Viewport = { x: 0, y: 0, zoom: 0.9 };
const MIN_ZOOM = 0.3;
const MAX_ZOOM = 1.6;
const CTRL_WHEEL_ZOOM_SENSITIVITY = 0.0012;

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
  /**
   * Fires when React Flow's multi-selection changes (marquee, shift-click).
   * Ids are agent-node ids only — other kinds (context, planspace, op) are
   * filtered out because save-as-template only operates on agent nodes.
   */
  onMultiSelectionChange?: (nodeIds: string[]) => void;
  /**
   * Fires when the user right-clicks a node tile. The caller shows the
   * context menu; the callback receives the underlying agent-node id (or
   * null if the target is not an agent) plus the viewport-space
   * coordinates for positioning.
   */
  onAgentNodeContextMenu?: (nodeId: string | null, x: number, y: number) => void;
  /**
   * Fires when a template card is dropped on the canvas. The callback
   * receives the anchor node id (if the drop target was an agent tile)
   * and the raw template slug string that was dragged.
   */
  onTemplateDrop?: (slug: string, anchorNodeId: string | null) => void;
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
  contextBundlesByNodeId,
  knownPlanspaceIds,
  hiddenPlanspaceIds,
  activePlanspaceId,
  canCreateVirtual,
  initialLayoutHints,
  initialLayoutViewport,
  onSelectionChange,
  onMultiSelectionChange,
  onAgentNodeContextMenu,
  onTemplateDrop,
  onLayoutHintsChange,
}: CanvasProps) {
  const layoutHintsRef = useRef<Record<string, { x: number; y: number }>>(
    sanitizeLayoutHints(initialLayoutHints),
  );
  const initialViewportRef = useRef<Viewport | null>(
    sanitizeViewport(initialLayoutViewport),
  );
  const viewportRef = useRef<Viewport | null>(initialViewportRef.current);
  const liveViewportRef = useRef<Viewport>(initialViewportRef.current ?? DEFAULT_VIEWPORT);
  const pendingHintsRef = useRef<Record<string, { x: number; y: number }>>({});
  const pendingViewportRef = useRef<Viewport | null>(null);
  const flushTimerRef = useRef<number | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const { getViewport, setViewport } = useReactFlow();
  const [layoutHydrationVersion, setLayoutHydrationVersion] = useState(0);
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

  const built = useMemo(
    () =>
      buildGraph({
        nodes,
        activeNodeId,
        projectTitle,
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
      contextBundlesByNodeId,
      knownPlanspaceIds,
      hiddenPlanspaceIds,
      activePlanspaceId,
      canCreateVirtual,
      layoutHydrationVersion,
    ],
  );

  /* React Flow controlled state. We keep an internal copy so dragging is smooth
   * while still reflecting upstream prop changes (e.g. node_updated events). */
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(
    decorateSelection(built.rfNodes, selectedNodeId),
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
      // Carry over ``selected`` so React Flow's multi-selection (marquee /
      // shift-click) survives an upstream ``built.rfNodes`` swap. Without
      // this, every ``node_updated`` websocket event would reset the
      // selection to just the scalar single-select target.
      const selectedById = new Map(current.map((n) => [n.id, n.selected]));
      const next = built.rfNodes.map((n) => {
        let out: RFNode = n;
        if (!hydrateFromLayout) {
          const existing = positionById.get(n.id);
          if (
            existing &&
            (existing.x !== n.position.x || existing.y !== n.position.y)
          ) {
            out = { ...out, position: existing };
          }
        }
        const carried = selectedById.get(n.id);
        if (carried !== undefined && carried !== out.selected) {
          out = { ...out, selected: carried };
        }
        return out;
      });
      return decorateSelection(next, selectedNodeId);
    });
    if (hydrateFromLayout) {
      appliedLayoutHydrationVersionRef.current = layoutHydrationVersion;
    }
  }, [
    built.rfNodes,
    selectedNodeId,
    setRfNodes,
    layoutHydrationVersion,
  ]);

  /* Edges depend on hover (the `loads` lane fades in only for the hovered or
   * selected node), so they get a separate effect. */
  useEffect(() => {
    setRfEdges(decorateEdges(built.rfEdges, selectedNodeId, hoveredNodeId));
  }, [built.rfEdges, selectedNodeId, hoveredNodeId, setRfEdges]);

  /* Persist drag positions client-side so they survive node updates, and
   * debounce a push to the backend so refreshes survive too. */
  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      let shouldFlush = false;
      for (const ch of changes) {
        if (ch.type === "position" && ch.position && ch.dragging !== undefined) {
          const position = { x: ch.position.x, y: ch.position.y };
          layoutHintsRef.current[ch.id] = position;
          pendingHintsRef.current[ch.id] = position;
          if (ch.dragging === false) shouldFlush = true;
        }
      }
      if (shouldFlush) scheduleFlushLayout(0);
      onNodesChange(changes);
    },
    [onNodesChange, scheduleFlushLayout],
  );

  const onMove = useCallback((_event: MouseEvent | TouchEvent | null, viewport: Viewport) => {
    const next = sanitizeViewport(viewport);
    if (next) liveViewportRef.current = next;
  }, []);

  const onMoveEnd = useCallback(
    (event: MouseEvent | TouchEvent | null, viewport: Viewport) => {
      /* Programmatic fitView can report a move-end without a user event. Keep
       * saved viewports user-owned so auto-fit does not overwrite them. */
      if (!event) return;
      const next = sanitizeViewport(viewport);
      if (!next || sameViewport(viewportRef.current, next)) return;
      liveViewportRef.current = next;
      viewportRef.current = next;
      pendingViewportRef.current = next;
      scheduleFlushLayout(250);
    },
    [scheduleFlushLayout],
  );

  const onWheelCapture = useCallback(
    (event: React.WheelEvent<HTMLDivElement>) => {
      if (!event.ctrlKey && !event.metaKey) return;
      if (!wrapperRef.current) return;
      event.preventDefault();
      event.stopPropagation();

      const rect = wrapperRef.current.getBoundingClientRect();
      const localX = event.clientX - rect.left;
      const localY = event.clientY - rect.top;
      const viewport = liveViewportRef.current;
      const normalizedDeltaY = normalizeWheelDeltaY(event);
      if (!Number.isFinite(normalizedDeltaY) || normalizedDeltaY === 0) return;

      const nextZoom = clamp(
        viewport.zoom * Math.exp(-normalizedDeltaY * CTRL_WHEEL_ZOOM_SENSITIVITY),
        MIN_ZOOM,
        MAX_ZOOM,
      );
      if (Math.abs(nextZoom - viewport.zoom) < 0.0001) return;

      const flowX = (localX - viewport.x) / viewport.zoom;
      const flowY = (localY - viewport.y) / viewport.zoom;
      const next = {
        x: localX - flowX * nextZoom,
        y: localY - flowY * nextZoom,
        zoom: nextZoom,
      };

      liveViewportRef.current = next;
      viewportRef.current = next;
      pendingViewportRef.current = next;
      setViewport(next, { duration: 0 });
      scheduleFlushLayout(250);
    },
    [scheduleFlushLayout, setViewport],
  );

  const persistCurrentViewport = useCallback(() => {
    window.setTimeout(() => {
      const next = sanitizeViewport(getViewport());
      if (!next || sameViewport(viewportRef.current, next)) return;
      liveViewportRef.current = next;
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
      }
    },
    [onSelectionChange],
  );

  /* Empty-canvas tap: clear selection. */
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

  /* Multi-selection observer. React Flow owns its own selection state; we
   * only translate agent-node ids out so callers can drive right-click and
   * library-dock actions. Non-agent selections (context, planspace, op,
   * projectRoot) don't participate in save-as-template. */
  useOnSelectionChange({
    onChange: ({ nodes: selNodes }) => {
      if (!onMultiSelectionChange) return;
      const agentIds: string[] = [];
      for (const n of selNodes) {
        const rf = n as RFNode;
        if (rf.type === "agent") {
          const data = rf.data as import("./layout").AgentNodeData;
          agentIds.push(data.node.id);
        }
      }
      onMultiSelectionChange(agentIds);
    },
  });

  const onNodeContextMenu = useCallback<NodeMouseHandler>(
    (event, node) => {
      event.preventDefault();
      const rf = node as RFNode;
      let agentId: string | null = null;
      if (rf.type === "agent") {
        const data = rf.data as import("./layout").AgentNodeData;
        agentId = data.node.id;
      }
      onAgentNodeContextMenu?.(agentId, event.clientX, event.clientY);
    },
    [onAgentNodeContextMenu],
  );

  const onCanvasDragOver = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    if (
      event.dataTransfer.types.includes("application/x-miniclaw-template")
    ) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    }
  }, []);

  const onCanvasDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      const slug = event.dataTransfer.getData("application/x-miniclaw-template");
      if (!slug) return;
      event.preventDefault();
      // Walk up the DOM from the drop target to find the nearest React Flow
      // node element and read its data-id. If none, the drop hit the pane.
      let anchorNodeId: string | null = null;
      let cursor = event.target as HTMLElement | null;
      while (cursor && cursor !== event.currentTarget) {
        const dataId = cursor.getAttribute?.("data-id");
        if (dataId) {
          // Match agent nodes only — the RF node id is the layout key,
          // which happens to equal the backend node id for agent tiles.
          const found = built.rfNodes.find(
            (n) => n.id === dataId && n.type === "agent",
          );
          if (found) {
            const data = found.data as import("./layout").AgentNodeData;
            anchorNodeId = data.node.id;
          }
          break;
        }
        cursor = cursor.parentElement;
      }
      onTemplateDrop?.(slug, anchorNodeId);
    },
    [built.rfNodes, onTemplateDrop],
  );

  return (
    <div
      ref={wrapperRef}
      className="relative h-full w-full"
      onWheelCapture={onWheelCapture}
      onDragOver={onCanvasDragOver}
      onDrop={onCanvasDrop}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        onNodesChange={handleNodesChange}
        onNodeClick={onNodeClick}
        onNodeContextMenu={onNodeContextMenu}
        onNodeMouseEnter={onNodeMouseEnter}
        onNodeMouseLeave={onNodeMouseLeave}
        onPaneClick={onPaneClick}
        onMove={onMove}
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
        defaultViewport={initialViewportRef.current ?? DEFAULT_VIEWPORT}
        minZoom={MIN_ZOOM}
        maxZoom={MAX_ZOOM}
        zoomOnScroll={false}
        panOnScroll
        panOnDrag
        selectionOnDrag={false}
        selectNodesOnDrag={false}
        multiSelectionKeyCode="Shift"
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

function normalizeWheelDeltaY(event: React.WheelEvent): number {
  if (event.deltaMode === 1) return event.deltaY * 16;
  if (event.deltaMode === 2) return event.deltaY * 600;
  return event.deltaY;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function decorateSelection(
  nodes: RFNode[],
  selectedNodeId: string | null,
): Node[] {
  return nodes.map((n) => {
    // OR the primary-click selection with whatever React Flow already tracks
    // (multi-select). We never clear ``selected`` from here — deselection is
    // driven by React Flow's own change events (pane click, replace-on-click).
    const desired = n.id === selectedNodeId || n.selected === true;
    return n.selected === desired ? n : { ...n, selected: desired };
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
