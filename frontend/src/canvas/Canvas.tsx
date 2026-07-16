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

import type { CommitDescriptor, ContextBundle, NodeInfo } from "../types";
import { artifactRawUrl } from "../api";
import {
  buildGraph,
  type RFNode,
  type RFEdge,
  type SkillEnumeration,
} from "./layout";
import { AgentNode } from "./nodes/AgentNode";
import { OpNode } from "./nodes/OpNode";
import { ContextNode } from "./nodes/ContextNode";
import { ProjectRootNode } from "./nodes/ProjectRootNode";
import { PlanspaceLaneNode } from "./nodes/PlanspaceLaneNode";
import {
  DependencyEdge,
  LoadsEdge,
  ProducesEdge,
  OpChevronEdge,
  ResumeEdge,
  TimelineEdge,
  setOpChevronContext,
} from "./edges/TimelineEdge";
import { ErrorTerminalNode } from "./nodes/ErrorTerminalNode";
import { ArtifactNode } from "./nodes/ArtifactNode";
import { CommitNode } from "./nodes/CommitNode";

const NODE_TYPES = {
  agent: AgentNode,
  op: OpNode,
  context: ContextNode,
  projectRoot: ProjectRootNode,
  planspaceLane: PlanspaceLaneNode,
  errorTerminal: ErrorTerminalNode,
  artifact: ArtifactNode,
  commit: CommitNode,
};

const EDGE_TYPES = {
  dependency: DependencyEdge,
  timeline: TimelineEdge,
  resume: ResumeEdge,
  loads: LoadsEdge,
  produces: ProducesEdge,
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
  | { kind: "artifact"; nodeId: string; name: string; ext: "md" | "json" | "html" }
  | { kind: "projectRoot" }
  | { kind: "commit"; sha: string | null }
  | { kind: "none" };

export type CanvasProps = {
  nodes: NodeInfo[];
  sessionId: string;
  selectedNodeId: string | null;
  activeNodeIds: string[];
  projectTitle: string;
  contextBundlesByNodeId: Record<string, ContextBundle | null | undefined>;
  knownPlanspaceIds: string[];
  hiddenPlanspaceIds: string[];
  activePlanspaceId: string | null;
  canCreateVirtual: boolean;
  /** User-wide skills enumerated from GET /skills. Dimmed on the shelf when
   * no live node has loaded them. */
  skills?: SkillEnumeration[];
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
  /**
   * Fires when a skill chip is dragged from a shelf tile and dropped onto
   * a virtual agent tile. The callback receives the virtual node id and
   * the skill plug id (``skills.<slug>``) to attach.
   */
  onAttachSkillToVirtual?: (virtualNodeId: string, skillId: string) => void;
  /** Called after drag-end / pan / zoom with layout state that changed. */
  onLayoutHintsChange?: (
    updates: Record<string, { x: number; y: number }>,
    viewport?: Viewport | null,
  ) => void;
  gitCommits?: CommitDescriptor[];
  gitHead?: string | null;
  gitDirtyCount?: number;
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
  sessionId,
  selectedNodeId,
  activeNodeIds,
  projectTitle,
  contextBundlesByNodeId,
  knownPlanspaceIds,
  hiddenPlanspaceIds,
  activePlanspaceId,
  canCreateVirtual,
  skills,
  initialLayoutHints,
  initialLayoutViewport,
  onSelectionChange,
  onMultiSelectionChange,
  onAgentNodeContextMenu,
  onTemplateDrop,
  onAttachSkillToVirtual,
  onLayoutHintsChange,
  gitCommits,
  gitHead,
  gitDirtyCount,
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
  const previousPrimarySelectionRef = useRef(selectedNodeId);
  const primarySelectionRef = useRef(selectedNodeId);
  primarySelectionRef.current = selectedNodeId;
  const pendingUserSelectionRef = useRef<{
    nodeId: string | null;
    preserveExisting: boolean;
  } | null>(null);

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
        activeNodeIds,
        projectTitle,
        layoutHints: layoutHintsRef.current,
        contextBundlesByNodeId,
        knownPlanspaceIds,
        hiddenPlanspaceIds,
        activePlanspaceId,
        canCreateVirtual,
        skills,
        gitCommits,
        gitHead,
        gitDirtyCount,
      }),
    [
      nodes,
      activeNodeIds,
      projectTitle,
      contextBundlesByNodeId,
      knownPlanspaceIds,
      hiddenPlanspaceIds,
      activePlanspaceId,
      canCreateVirtual,
      skills,
      gitCommits,
      gitHead,
      gitDirtyCount,
      layoutHydrationVersion,
    ],
  );
  const syncedBuiltNodesRef = useRef(built.rfNodes);

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
    if (
      syncedBuiltNodesRef.current === built.rfNodes &&
      !hydrateFromLayout
    ) {
      return;
    }
    syncedBuiltNodesRef.current = built.rfNodes;
    setRfNodes((current) => {
      const runtimeById = new Map(current.map((n) => [n.id, n]));
      // Carry over ``selected`` so React Flow's multi-selection (marquee /
      // shift-click) survives an upstream ``built.rfNodes`` swap. Without
      // this, every ``node_updated`` websocket event would reset the
      // selection to just the scalar single-select target.
      const selectedById = new Map(current.map((n) => [n.id, n.selected]));
      const next = built.rfNodes.map((n) => {
        const runtime = runtimeById.get(n.id);
        let out: RFNode = runtime ? { ...runtime, ...n } : n;
        if (!hydrateFromLayout) {
          const existing = runtime?.position;
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
      return decorateSelection(
        next,
        primarySelectionRef.current,
        true,
      );
    });
    if (hydrateFromLayout) {
      appliedLayoutHydrationVersionRef.current = layoutHydrationVersion;
    }
  }, [
    built.rfNodes,
    setRfNodes,
    layoutHydrationVersion,
  ]);

  useEffect(() => {
    const primarySelectionChanged =
      previousPrimarySelectionRef.current !== selectedNodeId;
    const pendingUserSelection = pendingUserSelectionRef.current;
    const preserveExistingSelection =
      pendingUserSelection?.nodeId === selectedNodeId &&
      pendingUserSelection.preserveExisting;
    previousPrimarySelectionRef.current = selectedNodeId;
    pendingUserSelectionRef.current = null;
    if (!primarySelectionChanged) return;
    setRfNodes((current) =>
      decorateSelection(
        current as RFNode[],
        selectedNodeId,
        preserveExistingSelection,
      ),
    );
  }, [selectedNodeId, setRfNodes]);

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
    (event, node) => {
      const n = node as RFNode;
      if (n.type === "agent") {
        const data = n.data as import("./layout").AgentNodeData;
        pendingUserSelectionRef.current = {
          nodeId: data.node.id,
          preserveExisting: event.shiftKey,
        };
        onSelectionChange({ kind: "agent", nodeId: data.node.id });
      } else if (n.type === "op") {
        const data = n.data as import("./layout").OpNodeData;
        pendingUserSelectionRef.current = {
          nodeId: data.node.id,
          preserveExisting: event.shiftKey,
        };
        onSelectionChange({ kind: "op", nodeId: data.node.id });
      } else if (n.type === "context") {
        const data = n.data as import("./layout").ContextNodeData;
        pendingUserSelectionRef.current = {
          nodeId: n.id,
          preserveExisting: event.shiftKey,
        };
        onSelectionChange({
          kind: "context",
          identityKey: data.identityKey,
          path: data.path,
          scope: data.scope,
          sourceKind: data.kind,
          plugId: data.plugId ?? null,
        });
      } else if (n.type === "projectRoot") {
        pendingUserSelectionRef.current = {
          nodeId: n.id,
          preserveExisting: event.shiftKey,
        };
        onSelectionChange({ kind: "projectRoot" });
      } else if (n.type === "commit") {
        const data = n.data as import("./layout").CommitNodeData;
        onSelectionChange({ kind: "commit", sha: data.ghost ? null : data.commit.sha });
      } else if (n.type === "planspaceLane") {
        const data = n.data as import("./layout").PlanspaceLaneData;
        pendingUserSelectionRef.current = {
          nodeId: n.id,
          preserveExisting: event.shiftKey,
        };
        onSelectionChange({ kind: "planspace", planspaceId: data.planspaceId });
      } else if (n.type === "errorTerminal") {
        const data = n.data as import("./layout").ErrorTerminalData;
        /* The terminal itself has no panel — selecting it focuses its owner. */
        pendingUserSelectionRef.current = {
          nodeId: data.ownerNodeId,
          preserveExisting: event.shiftKey,
        };
        onSelectionChange({
          kind: "agent",
          nodeId: data.ownerNodeId,
        });
      } else if (n.type === "artifact") {
        const data = n.data as import("./layout").ArtifactNodeData;
        if (!data.artifact) {
          onSelectionChange({ kind: "agent", nodeId: data.ownerNodeId });
          return;
        }
        const ext = data.artifact.name.split(".").pop() as "md" | "json" | "html";
        pendingUserSelectionRef.current = {
          nodeId: n.id,
          preserveExisting: event.shiftKey,
        };
        if (ext === "html") {
          window.open(
            artifactRawUrl(sessionId, data.ownerNodeId, data.artifact.name),
            "_blank",
            "noopener",
          );
        }
        onSelectionChange({
          kind: "artifact",
          nodeId: data.ownerNodeId,
          name: data.artifact.name,
          ext,
        });
      }
    },
    [onSelectionChange, sessionId],
  );

  /* Empty-canvas tap: clear selection. */
  const onPaneClick = useCallback(
    (event: React.MouseEvent) => {
      const target = event.target as HTMLElement;
      const isPane =
        target.classList.contains("react-flow__pane") ||
        target.classList.contains("react-flow__renderer");
      if (!isPane) return;
      pendingUserSelectionRef.current = {
        nodeId: null,
        preserveExisting: false,
      };
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
    const types = event.dataTransfer.types;
    if (
      types.includes("application/x-miniclaw-template") ||
      types.includes("application/x-miniclaw-skill")
    ) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    }
  }, []);

  const onCanvasDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      const templateSlug = event.dataTransfer.getData(
        "application/x-miniclaw-template",
      );
      const skillId = event.dataTransfer.getData(
        "application/x-miniclaw-skill",
      );
      if (!templateSlug && !skillId) return;
      event.preventDefault();
      // Walk up the DOM from the drop target to find the nearest React Flow
      // node element and read its data-id. If none, the drop hit the pane.
      let anchorAgentId: string | null = null;
      let anchorNode: RFNode | null = null;
      let cursor = event.target as HTMLElement | null;
      while (cursor && cursor !== event.currentTarget) {
        const dataId = cursor.getAttribute?.("data-id");
        if (dataId) {
          const found = built.rfNodes.find((n) => n.id === dataId);
          if (found) {
            anchorNode = found;
            if (found.type === "agent") {
              const data = found.data as import("./layout").AgentNodeData;
              anchorAgentId = data.node.id;
            }
          }
          break;
        }
        cursor = cursor.parentElement;
      }
      if (skillId) {
        /* Skills are attach-to-virtual only. Dropping on the pane, a
         * running agent, an op node, or a context tile is a no-op — the
         * cursor gives no feedback, but the drop is simply ignored. */
        if (anchorNode?.type === "agent") {
          const data = anchorNode.data as import("./layout").AgentNodeData;
          if (data.node.state === "virtual" && !data.node.obsolete_reason) {
            onAttachSkillToVirtual?.(data.node.id, skillId);
          }
        }
        return;
      }
      onTemplateDrop?.(templateSlug, anchorAgentId);
    },
    [built.rfNodes, onTemplateDrop, onAttachSkillToVirtual],
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
        elevateNodesOnSelect={false}
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
      </ReactFlow>
    </div>
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
  preserveExisting = false,
): Node[] {
  return nodes.map((n) => {
    const desired =
      n.id === selectedNodeId || (preserveExisting && n.selected === true);
    return n.selected === desired ? n : { ...n, selected: desired };
  });
}

function decorateEdges(
  edges: RFEdge[],
  selectedNodeId: string | null,
  hoveredNodeId: string | null,
): Edge[] {
  return edges.map((e) => {
    if (e.type === "loads" || e.type === "produces") {
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
