import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlowProvider,
  applyNodeChanges,
  useOnSelectionChange,
  type Node,
  type NodeChange,
  type NodeMouseHandler,
  type Viewport,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "reactflow";
import "reactflow/dist/style.css";

import type {
  CommitDescriptor,
  ContextBundle,
  NodeInfo,
  SessionHost,
  TemplateInstanceRecord,
} from "../types";
import { artifactRawUrl } from "../api";
import {
  buildGraph,
  classifyPlanspaceLaneResizes,
  resolveCommitPositionTransfer,
  resolveGitChangesAppearancePosition,
  resizePlanspaceLanes,
  snapPlanspaceChildPosition,
  type RFNode,
  type PrincipleEnumeration,
  type SkillEnumeration,
} from "./layout";
import { AgentNode } from "./nodes/AgentNode";
import { OpNode } from "./nodes/OpNode";
import { ContextNode } from "./nodes/ContextNode";
import { PlanspaceLaneNode } from "./nodes/PlanspaceLaneNode";
import { TemplateGroupNode } from "./nodes/TemplateGroupNode";
import { TemplateInstanceBoxNode } from "./nodes/TemplateInstanceBoxNode";
import {
  DependencyEdge,
  LoadsEdge,
  ProducesEdge,
  ResumeEdge,
  TimelineEdge,
} from "./edges/TimelineEdge";
import { CommitEdge } from "./edges/CommitEdge";
import { ErrorTerminalNode } from "./nodes/ErrorTerminalNode";
import { ArtifactNode } from "./nodes/ArtifactNode";
import { CommitNode } from "./nodes/CommitNode";
import { CommitColumnHeaderNode } from "./nodes/CommitColumnHeaderNode";
import { setHoverGroup } from "./hoverStore";
import { decorateEdges, resolveHoverGroup } from "./edgeVisibility";
import { decoratePendingGateLayers } from "./nodeLayers";

const NODE_TYPES = {
  agent: AgentNode,
  op: OpNode,
  context: ContextNode,
  planspaceLane: PlanspaceLaneNode,
  templateGroup: TemplateGroupNode,
  templateInstanceBox: TemplateInstanceBoxNode,
  errorTerminal: ErrorTerminalNode,
  artifact: ArtifactNode,
  commit: CommitNode,
  commitColumnHeader: CommitColumnHeaderNode,
};

const EDGE_TYPES = {
  dependency: DependencyEdge,
  timeline: TimelineEdge,
  resume: ResumeEdge,
  loads: LoadsEdge,
  produces: ProducesEdge,
  commitTrunk: CommitEdge,
  commitLink: CommitEdge,
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
  | {
      kind: "templateInstance";
      instanceId: string;
      /** Implicit outputs — what a new downstream node attaches to (§4.3). */
      sinkNodeIds: string[];
      memberNodeIds: string[];
      collapsed: boolean;
    }
  | { kind: "artifact"; nodeId: string; name: string; ext: "md" | "json" | "html" }
  | { kind: "projectRoot" }
  | { kind: "commit"; sha: string | null }
  | { kind: "none" };

export type CanvasNodePositionTarget = {
  nodeId: string;
  position: { x: number; y: number };
};

export type CanvasProps = {
  nodes: NodeInfo[];
  sessionId: string;
  selectedNodeId: string | null;
  activeNodeIds: string[];
  /** Agent nodes whose inline request panel must stay above sibling nodes. */
  pendingGateNodeIds?: string[];
  contextBundlesByNodeId: Record<string, ContextBundle | null | undefined>;
  knownPlanspaceIds: string[];
  activatablePlanspaceIds: string[];
  hiddenPlanspaceIds: string[];
  activePlanspaceId: string | null;
  autoPlanspaceIds: string[];
  canCreateVirtual: boolean;
  /** Stamped instance records, for the group header's name and arguments. */
  templateInstances?: TemplateInstanceRecord[];
  /** Instances drawn as a single collapsed box. View state only. The collapse
   * toggle itself goes through `setTemplateGroupContext` /
   * `setTemplateInstanceBoxContext`, as the lane and agent tiles do. */
  collapsedTemplateInstanceIds?: string[];
  nodePositionTarget?: CanvasNodePositionTarget | null;
  onNodePositionTargetApplied?: (nodeId: string) => void;
  onCreateVirtualAt?: (
    planspaceId: string,
    position: { x: number; y: number },
  ) => void;
  /** Library entries used only to resolve visible observed/declared bindings. */
  principles?: PrincipleEnumeration[];
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
   * and the raw template slug string that was dragged. Dropping on a
   * collapsed instance instead supplies its sink node ids, which the caller
   * uses as the new nodes' dependencies (§4.3).
   */
  onTemplateDrop?: (
    slug: string,
    anchorNodeId: string | null,
    anchorSinkNodeIds?: string[],
  ) => void;
  /**
   * Fires when a principle card is dragged from Library and dropped onto
   * a virtual agent tile. The callback receives the virtual node id and
   * the principle plug id (``principles.<slug>``) to attach.
   */
  onAttachPrincipleToVirtual?: (virtualNodeId: string, principleId: string) => void;
  onAttachSkillToVirtual?: (virtualNodeId: string, skillId: string) => void;
  /** Called after drag-end / pan / zoom with layout state that changed. */
  onLayoutHintsChange?: (
    updates: Record<string, { x: number; y: number }>,
    viewport?: Viewport | null,
    remove?: string[],
  ) => void;
  gitCommits?: CommitDescriptor[];
  gitHead?: string | null;
  gitDirtyCount?: number;
  gitHosts?: SessionHost[];
  /** SHA produced by a commit explicitly started from this MiniClaw2 UI. */
  commitPositionTarget?: string | null;
  onCommitPositionTransferHandled?: (sha: string) => void;
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
  pendingGateNodeIds = [],
  contextBundlesByNodeId,
  knownPlanspaceIds,
  activatablePlanspaceIds,
  hiddenPlanspaceIds,
  activePlanspaceId,
  autoPlanspaceIds,
  canCreateVirtual,
  templateInstances,
  collapsedTemplateInstanceIds,
  nodePositionTarget,
  onNodePositionTargetApplied,
  onCreateVirtualAt,
  principles,
  skills,
  initialLayoutHints,
  initialLayoutViewport,
  onSelectionChange,
  onMultiSelectionChange,
  onAgentNodeContextMenu,
  onTemplateDrop,
  onAttachPrincipleToVirtual,
  onAttachSkillToVirtual,
  onLayoutHintsChange,
  gitCommits,
  gitHead,
  gitDirtyCount,
  gitHosts,
  commitPositionTarget = null,
  onCommitPositionTransferHandled,
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
  const pendingHintRemovalsRef = useRef<Set<string>>(new Set());
  const pendingViewportRef = useRef<Viewport | null>(null);
  const flushTimerRef = useRef<number | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const appliedNodePositionTargetRef = useRef<string | null>(null);
  const pendingNodePositionTargetRef = useRef<string | null>(null);
  if (
    nodePositionTarget &&
    appliedNodePositionTargetRef.current !== nodePositionTarget.nodeId
  ) {
    const position = { ...nodePositionTarget.position };
    layoutHintsRef.current[nodePositionTarget.nodeId] = position;
    pendingHintsRef.current[nodePositionTarget.nodeId] = position;
    pendingHintRemovalsRef.current.delete(nodePositionTarget.nodeId);
    appliedNodePositionTargetRef.current = nodePositionTarget.nodeId;
    pendingNodePositionTargetRef.current = nodePositionTarget.nodeId;
  }
  const [hoverGroup, setHoverGroupState] = useState<string[]>([]);
  const { getViewport, screenToFlowPosition, setViewport } = useReactFlow();
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
    const removals = [...pendingHintRemovalsRef.current];
    const pendingViewport = pendingViewportRef.current;
    if (Object.keys(pending).length === 0 && removals.length === 0 && !pendingViewport) return;
    pendingHintsRef.current = {};
    pendingHintRemovalsRef.current = new Set();
    pendingViewportRef.current = null;
    onLayoutHintsChange?.(pending, pendingViewport, removals);
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

  useEffect(() => {
    if (
      !nodePositionTarget ||
      pendingNodePositionTargetRef.current !== nodePositionTarget.nodeId
    ) {
      return;
    }
    pendingNodePositionTargetRef.current = null;
    scheduleFlushLayout(0);
    onNodePositionTargetApplied?.(nodePositionTarget.nodeId);
  }, [
    nodePositionTarget,
    onNodePositionTargetApplied,
    scheduleFlushLayout,
  ]);

  const built = useMemo(
    () =>
      buildGraph({
        nodes,
        activeNodeIds,
        layoutHints: layoutHintsRef.current,
        contextBundlesByNodeId,
        knownPlanspaceIds,
        activatablePlanspaceIds,
        hiddenPlanspaceIds,
        activePlanspaceId,
        autoPlanspaceIds,
        canCreateVirtual,
        templateInstances,
        collapsedTemplateInstanceIds,
        principles,
        skills,
        gitCommits,
        gitHead,
        gitDirtyCount,
        gitHosts,
      }),
    [
      nodes,
      activeNodeIds,
      contextBundlesByNodeId,
      knownPlanspaceIds,
      activatablePlanspaceIds,
      hiddenPlanspaceIds,
      activePlanspaceId,
      autoPlanspaceIds,
      canCreateVirtual,
      templateInstances,
      collapsedTemplateInstanceIds,
      principles,
      skills,
      gitCommits,
      gitHead,
      gitDirtyCount,
      gitHosts,
      layoutHydrationVersion,
    ],
  );
  /* Read imperatively by onNodeClick, which must not be re-created on every
   * rebuild — the group frame only carries its instance id, so the click needs
   * the current cluster to report members and sinks. */
  const builtRef = useRef(built);
  builtRef.current = built;
  const layeredBuiltNodes = useMemo(
    () => decoratePendingGateLayers(built.rfNodes, pendingGateNodeIds),
    [built.rfNodes, pendingGateNodeIds],
  );
  const syncedBuiltNodesRef = useRef(layeredBuiltNodes);

  /* React Flow controlled state. We keep an internal copy so dragging is smooth
   * while still reflecting upstream prop changes (e.g. node_updated events). */
  const [rfNodes, setRfNodes] = useNodesState(
    decorateSelection(layeredBuiltNodes, selectedNodeId),
  );
  const [rfEdges, setRfEdges] = useEdgesState(
    decorateEdges(built.rfEdges, selectedNodeId, hoverGroup),
  );
  const rfNodesRef = useRef(rfNodes);
  rfNodesRef.current = rfNodes;
  const commitGhostPositionRef = useRef<{ x: number; y: number } | null>(
    layoutHintsRef.current["commit:ghost"] ?? null,
  );
  const currentCommitGhost = rfNodes.find((node) => node.id === "commit:ghost");
  if (currentCommitGhost) {
    commitGhostPositionRef.current = {
      x: currentCommitGhost.position.x,
      y: currentCommitGhost.position.y,
    };
  }

  /* Sync upstream node changes into local state without trampling drag
   * positions. Critically, this effect must NOT depend on hover state — hover
   * does not change node identity, and forcing a node-list rewrite on every
   * mouseenter/leave makes React Flow's pointer hit-test churn enough to lose
   * its grip on which element is under the cursor, producing the cursor flicker
   * between pane (grab) and node (pointer). */
  useEffect(() => {
    const hydrateFromLayout =
      appliedLayoutHydrationVersionRef.current !== layoutHydrationVersion;
    const commitPositionTransfer = resolveCommitPositionTransfer(
      rfNodesRef.current as RFNode[],
      layeredBuiltNodes,
      commitPositionTarget,
      commitGhostPositionRef.current,
    );
    const gitChangesAppearancePosition = commitPositionTransfer
      ? null
      : resolveGitChangesAppearancePosition(
          rfNodesRef.current as RFNode[],
          layeredBuiltNodes,
        );
    if (
      syncedBuiltNodesRef.current === layeredBuiltNodes &&
      !hydrateFromLayout &&
      !commitPositionTransfer &&
      !gitChangesAppearancePosition
    ) {
      return;
    }
    syncedBuiltNodesRef.current = layeredBuiltNodes;
    if (commitPositionTransfer) {
      layoutHintsRef.current[commitPositionTransfer.toId] = commitPositionTransfer.position;
      delete layoutHintsRef.current[commitPositionTransfer.fromId];
      pendingHintsRef.current[commitPositionTransfer.toId] = commitPositionTransfer.position;
      delete pendingHintsRef.current[commitPositionTransfer.fromId];
      pendingHintRemovalsRef.current.add(commitPositionTransfer.fromId);
      pendingHintRemovalsRef.current.delete(commitPositionTransfer.toId);
      scheduleFlushLayout(0);
    }
    if (gitChangesAppearancePosition) {
      layoutHintsRef.current["commit:ghost"] = gitChangesAppearancePosition;
      pendingHintsRef.current["commit:ghost"] = gitChangesAppearancePosition;
      pendingHintRemovalsRef.current.delete("commit:ghost");
      scheduleFlushLayout(0);
    }
    setRfNodes((current) => {
      const runtimeById = new Map(current.map((n) => [n.id, n]));
      // Carry over ``selected`` so React Flow's multi-selection (marquee /
      // shift-click) survives an upstream ``built.rfNodes`` swap. Without
      // this, every ``node_updated`` websocket event would reset the
      // selection to just the scalar single-select target.
      const selectedById = new Map(current.map((n) => [n.id, n.selected]));
      const next = layeredBuiltNodes.map((n) => {
        const runtime = runtimeById.get(n.id);
        // zIndex needs an explicit assignment: spreading a fresh layout node
        // without that property would otherwise retain a previously elevated
        // pending-gate layer from `runtime` after the request is resolved.
        let out: RFNode = runtime
          ? { ...runtime, ...n, zIndex: n.zIndex }
          : n;
        /* Fresh graph nodes carry bootstrap dimensions, while React Flow's
         * runtime copy contains the measured DOM size. Preserve the measured
         * child geometry across status-driven graph rebuilds so lane fitting
         * uses the same bounds as drag-stop fitting. Lanes and template group
         * frames are excluded: both are sized by layout from their children's
         * bounds, so the computed value is the authoritative one. */
        if (runtime && n.type !== "planspaceLane" && n.type !== "templateGroup") {
          out = {
            ...out,
            width: runtime.width ?? n.width,
            height: runtime.height ?? n.height,
          };
        }
        /* Same reason: a group frame is derived, never dragged, so a rebuild
         * must be free to move it when its members move. */
        if (!hydrateFromLayout && n.type !== "templateGroup") {
          const existing = runtime?.position;
          if (
            existing &&
            !(
              commitPositionTransfer?.resetGhostPosition &&
              n.id === commitPositionTransfer.fromId
            ) &&
            (existing.x !== n.position.x || existing.y !== n.position.y)
          ) {
            out = { ...out, position: existing };
          }
        }
        if (n.id === commitPositionTransfer?.toId) {
          out = { ...out, position: commitPositionTransfer.position };
        }
        if (
          n.id === commitPositionTransfer?.fromId &&
          commitPositionTransfer.resetGhostPosition
        ) {
          out = { ...out, position: commitPositionTransfer.resetGhostPosition };
        }
        if (n.id === "commit:ghost" && gitChangesAppearancePosition) {
          out = { ...out, position: gitChangesAppearancePosition };
        }
        const carried = selectedById.get(n.id);
        if (carried !== undefined && carried !== out.selected) {
          out = { ...out, selected: carried };
        }
        return out;
      });
      const laneIds = new Set(
        next
          .filter((node) => node.type === "planspaceLane")
          .map((node) => node.id),
      );
      return decorateSelection(
        resizePlanspaceLanes(
          next,
          laneIds,
          true,
          layoutHintsRef.current,
        ),
        primarySelectionRef.current,
        true,
      );
    });
    if (hydrateFromLayout) {
      appliedLayoutHydrationVersionRef.current = layoutHydrationVersion;
    }
    if (commitPositionTransfer && commitPositionTarget) {
      onCommitPositionTransferHandled?.(commitPositionTarget);
    }
  }, [
    layeredBuiltNodes,
    setRfNodes,
    layoutHydrationVersion,
    scheduleFlushLayout,
    commitPositionTarget,
    onCommitPositionTransferHandled,
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
    setRfEdges(decorateEdges(built.rfEdges, selectedNodeId, hoverGroup));
  }, [built.rfEdges, selectedNodeId, hoverGroup, setRfEdges]);

  /* Persist drag positions client-side so they survive node updates. Lane
   * chrome grows during a drag and shrink-fits on drop, but is derived local
   * state rather than another persisted layout hint. */
  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      let shouldFlush = false;
      for (const change of changes) {
        if (change.type !== "position") continue;
        if (change.position && change.dragging !== undefined) {
          const position = { x: change.position.x, y: change.position.y };
          layoutHintsRef.current[change.id] = position;
          pendingHintsRef.current[change.id] = position;
          pendingHintRemovalsRef.current.delete(change.id);
        }
        if (change.dragging === false) shouldFlush = true;
      }
      if (shouldFlush) scheduleFlushLayout(0);
      setRfNodes((current) => {
        const { growLaneIds, fitLaneIds } = classifyPlanspaceLaneResizes(
          current as RFNode[],
          changes,
        );
        let next = applyNodeChanges(changes, current) as RFNode[];
        next = resizePlanspaceLanes(
          next,
          growLaneIds,
          false,
          layoutHintsRef.current,
        );
        return resizePlanspaceLanes(
          next,
          fitLaneIds,
          true,
          layoutHintsRef.current,
        );
      });
    },
    [scheduleFlushLayout, setRfNodes],
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
      } else if (n.type === "commit") {
        const data = n.data as import("./layout").CommitNodeData;
        pendingUserSelectionRef.current = {
          nodeId: data.ghost ? "commit:ghost" : `commit:${data.commit.sha}`,
          preserveExisting: event.shiftKey,
        };
        onSelectionChange({ kind: "commit", sha: data.ghost ? null : data.commit.sha });
      } else if (n.type === "planspaceLane") {
        const data = n.data as import("./layout").PlanspaceLaneData;
        pendingUserSelectionRef.current = {
          nodeId: n.id,
          preserveExisting: event.shiftKey,
        };
        onSelectionChange({ kind: "planspace", planspaceId: data.planspaceId });
      } else if (n.type === "templateGroup") {
        const data = n.data as import("./layout").TemplateGroupData;
        pendingUserSelectionRef.current = {
          nodeId: n.id,
          preserveExisting: event.shiftKey,
        };
        const cluster = builtRef.current.templateInstances[data.instanceId];
        onSelectionChange({
          kind: "templateInstance",
          instanceId: data.instanceId,
          sinkNodeIds: cluster?.sinkNodeIds ?? [],
          memberNodeIds: cluster?.memberNodeIds ?? [],
          /* The expanded frame only accepts pointer events on its header, and
           * that same click collapses it. Select the box that will replace the
           * frame so the highlight follows the interaction. */
          collapsed: true,
        });
      } else if (n.type === "templateInstanceBox") {
        const data = n.data as import("./layout").TemplateInstanceBoxData;
        pendingUserSelectionRef.current = {
          nodeId: n.id,
          preserveExisting: event.shiftKey,
        };
        onSelectionChange({
          kind: "templateInstance",
          instanceId: data.instanceId,
          sinkNodeIds: data.sinkNodeIds,
          memberNodeIds: data.memberNodeIds,
          collapsed: true,
        });
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

  const onCanvasDoubleClick = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      if (
        event.button !== 0 ||
        !canCreateVirtual ||
        !activePlanspaceId ||
        !onCreateVirtualAt
      ) {
        return;
      }
      const target = event.target as HTMLElement;
      if (!target.classList.contains("react-flow__pane")) return;

      const flowPosition = screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });
      const lane = rfNodesRef.current.find(
        (node) => node.id === `planspace:${activePlanspaceId}`,
      );
      if (!lane || lane.type !== "planspaceLane") return;
      const data = lane.data as import("./layout").PlanspaceLaneData;
      const width = lane.width ?? data.width;
      const height = lane.height ?? data.height;
      if (
        flowPosition.x < lane.position.x ||
        flowPosition.x > lane.position.x + width ||
        flowPosition.y < lane.position.y ||
        flowPosition.y > lane.position.y + height
      ) {
        return;
      }

      event.preventDefault();
      onCreateVirtualAt(
        activePlanspaceId,
        snapPlanspaceChildPosition(flowPosition, lane.position),
      );
    },
    [
      activePlanspaceId,
      canCreateVirtual,
      onCreateVirtualAt,
      screenToFlowPosition,
    ],
  );

  const onNodeMouseEnter = useCallback<NodeMouseHandler>((_, node) => {
    const group = resolveHoverGroup(
      node.id,
      built.epochMembersByCommitSha,
      built.commitHubIdByNodeId,
    );
    setHoverGroupState(group);
    setHoverGroup(group);
  }, [built.commitHubIdByNodeId, built.epochMembersByCommitSha]);
  const onNodeMouseLeave = useCallback<NodeMouseHandler>(() => {
    setHoverGroupState([]);
    setHoverGroup([]);
  }, []);

  useEffect(() => () => setHoverGroup([]), []);

  /* Multi-selection observer. React Flow owns its own selection state; we
   * only translate agent-node ids out so callers can drive right-click and
   * library-dock actions. Non-agent selections don't participate in
   * save-as-template. */
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
      types.includes("application/x-miniclaw-principle") ||
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
      const principleId = event.dataTransfer.getData(
        "application/x-miniclaw-principle",
      );
      const skillId = event.dataTransfer.getData("application/x-miniclaw-skill");
      if (!templateSlug && !principleId && !skillId) return;
      event.preventDefault();
      // Walk up the DOM from the drop target to find the nearest React Flow
      // node element and read its data-id. If none, the drop hit the pane.
      let anchorAgentId: string | null = null;
      let anchorNode: RFNode | null = null;
      /* A drop on a collapsed instance anchors to its sinks (§4.3) rather than
       * to a node the user cannot see. */
      let anchorSinkNodeIds: string[] = [];
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
            } else if (found.type === "templateInstanceBox") {
              const data = found.data as import("./layout").TemplateInstanceBoxData;
              anchorSinkNodeIds = data.sinkNodeIds;
            }
          }
          break;
        }
        cursor = cursor.parentElement;
      }
      if (principleId) {
        /* Principles are attach-to-virtual only. Dropping on the pane, a
         * running agent, an op node, or a context tile is a no-op — the
         * cursor gives no feedback, but the drop is simply ignored. */
        if (anchorNode?.type === "agent") {
          const data = anchorNode.data as import("./layout").AgentNodeData;
          if (data.node.state === "virtual" && !data.node.obsolete_reason) {
            onAttachPrincipleToVirtual?.(data.node.id, principleId);
          }
        }
        return;
      }
      if (skillId) {
        if (anchorNode?.type === "agent") {
          const data = anchorNode.data as import("./layout").AgentNodeData;
          if (data.node.state === "virtual" && !data.node.obsolete_reason) {
            onAttachSkillToVirtual?.(data.node.id, skillId);
          }
        }
        return;
      }
      onTemplateDrop?.(templateSlug, anchorAgentId, anchorSinkNodeIds);
    },
    [built.rfNodes, onTemplateDrop, onAttachPrincipleToVirtual, onAttachSkillToVirtual],
  );

  return (
    <div
      ref={wrapperRef}
      className="relative h-full w-full"
      onWheelCapture={onWheelCapture}
      onDoubleClick={onCanvasDoubleClick}
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
        zoomOnDoubleClick={false}
        panOnScroll
        panOnDrag={[2]}
        selectionOnDrag
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
