import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlowProvider,
  applyNodeChanges,
  useOnSelectionChange,
  useStoreApi,
  type EdgeMouseHandler,
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
  appendBelowLanePosition,
  availableLaneJumps,
  buildGraph,
  classifyPlanspaceLaneResizes,
  laneNeedsVerticalJump,
  resolveCommitPositionTransfer,
  resolveLaneJumpLeftOffset,
  resolveLaneVerticalSpan,
  resolveRenderedLaneAnchorId,
  resolveDisplacedGhostPosition,
  resolveGitChangesAppearancePosition,
  resolveSyncedNodePosition,
  resizePlanspaceLanes,
  snapPlanspaceChildPosition,
  templateInstanceBoxNodeId,
  type LaneVerticalSpan,
  type RFNode,
  type PrincipleEnumeration,
  type SkillEnumeration,
  type TemplatePortRecord,
} from "./layout";
import { AgentNode } from "./nodes/AgentNode";
import { OpNode } from "./nodes/OpNode";
import { ContextNode } from "./nodes/ContextNode";
import { PlanspaceLaneNode } from "./nodes/PlanspaceLaneNode";
import { TemplateGroupNode } from "./nodes/TemplateGroupNode";
import { TemplateInstanceBoxNode } from "./nodes/TemplateInstanceBoxNode";
import { TemplatePortNode } from "./nodes/TemplatePortNode";
import {
  DependencyEdge,
  LoadsEdge,
  ProducesEdge,
  ResumeEdge,
  TimelineEdge,
} from "./edges/TimelineEdge";
import { CommitEdge } from "./edges/CommitEdge";
import { WiringOverlay } from "./edges/WiringOverlay";
import {
  canDisconnectDependency,
  resolveWiringDrop,
} from "./dependencyWiring";
import {
  endWiringDrag,
  moveWiringPointer,
  useWiringDrag,
} from "./wiringDragStore";
import { ErrorTerminalNode } from "./nodes/ErrorTerminalNode";
import { ArtifactNode } from "./nodes/ArtifactNode";
import { CommitNode } from "./nodes/CommitNode";
import { CommitColumnHeaderNode } from "./nodes/CommitColumnHeaderNode";
import { setHoverGroup } from "./hoverStore";
import { setLaneAppendResolver } from "./lanePlacement";
import {
  decorateEdges,
  resolveHoverGroup,
  resolveInteractiveDependencyEdges,
  type EdgeDisconnectDecoration,
} from "./edgeVisibility";
import { decoratePendingGateLayers } from "./nodeLayers";
import {
  isRightDragPan,
  panViewportBy,
  shouldPanThroughRightDrag,
  type RightDragHit,
} from "./rightDragPan";

const NODE_TYPES = {
  agent: AgentNode,
  op: OpNode,
  context: ContextNode,
  planspaceLane: PlanspaceLaneNode,
  templateGroup: TemplateGroupNode,
  templateInstanceBox: TemplateInstanceBoxNode,
  templatePort: TemplatePortNode,
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

/** The dependency edge the user clicked, resolved to the pair a withdrawal
 * would rewrite. `edgeId` is the rendered id, which for a collapsed instance
 * differs from `sourceId`/`targetId`. */
type DisconnectTarget = {
  edgeId: string;
  sourceId: string;
  targetId: string;
  confirming: boolean;
};

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

/** A request to bring `nodeId` into view; `version` makes repeats distinct. */
export type CanvasCenterRequest = {
  nodeId: string;
  version: number;
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
  /** Input ports of the template being edited, when this project is an embedded
   * template session. Ordinary projects pass nothing and render unchanged. */
  templatePorts?: TemplatePortRecord[];
  /** Template argument names per node id, for the prompt-parameter chips. */
  templateArgumentsByNodeId?: Record<string, string[]>;
  nodePositionTarget?: CanvasNodePositionTarget | null;
  onNodePositionTargetApplied?: (nodeId: string) => void;
  /** Bring a node into view. Version-counted so repeat requests re-fire. */
  centerOnNodeRequest?: CanvasCenterRequest | null;
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
  /** Whether this host/session may rewrite a node owned dependency list. */
  canMutateNode?: (nodeId: string) => boolean;
  /**
   * Fires when the user drags a wire from one agent tile onto another. The
   * source is appended to the target's `scheduled_deps` — the target is the side
   * that owns the array, so a wire drawn A→B means "B depends on A".
   */
  onConnectDependency?: (targetNodeId: string, sourceNodeId: string) => void;
  /**
   * Fires when a wire is released on empty canvas: create a new draft virtual
   * that waits for `sourceNodeId`, positioned where the user let go.
   */
  onCreateDependencyVirtualAt?: (
    sourceNodeId: string,
    position: { x: number; y: number },
  ) => void;
  /** Fires when the user confirms withdrawing a dependency on the canvas. */
  onDisconnectDependency?: (targetNodeId: string, sourceNodeId: string) => void;
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
  templatePorts,
  templateArgumentsByNodeId,
  nodePositionTarget,
  onNodePositionTargetApplied,
  centerOnNodeRequest,
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
  canMutateNode = () => false,
  onConnectDependency,
  onCreateDependencyVirtualAt,
  onDisconnectDependency,
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
        templatePorts,
        templateArgumentsByNodeId,
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
      templatePorts,
      templateArgumentsByNodeId,
      principles,
      skills,
      gitCommits,
      gitHead,
      gitDirtyCount,
      gitHosts,
      layoutHydrationVersion,
      nodePositionTarget,
    ],
  );
  /* Read imperatively by onNodeClick, which must not be re-created on every
   * rebuild — the group frame only carries its instance id, so the click needs
   * the current cluster to report members and sinks. */
  const builtRef = useRef(built);
  builtRef.current = built;
  /* A member of a collapsed instance has no node of its own on the canvas —
   * it is drawn as part of the box. Centering on its raw id would find
   * nothing, so resolve to the box that stands in for it. Reads through the
   * ref so the identity stays stable across rebuilds. */
  const resolveRenderId = useCallback((nodeId: string): string => {
    const clusters = builtRef.current.templateInstances;
    for (const cluster of Object.values(clusters)) {
      if (!cluster.collapsed) continue;
      if (cluster.memberNodeIds.includes(nodeId)) {
        return templateInstanceBoxNodeId(cluster.instanceId);
      }
    }
    return nodeId;
  }, []);
  /* The inverse: an endpoint React Flow reports may be a collapsed instance box
   * standing in for several members, so a drag on it has to be resolved back to
   * a durable node id before it can mean anything to `scheduled_deps`.
   *
   * As a source, the box means its sinks — the members nothing inside the
   * instance consumes. One sink resolves cleanly; several would make a single
   * drag write several dependencies, which is the create-downstream gesture's
   * job, not this one's. As a target it never resolves: the array cannot say
   * "depends on this whole instance", and members are usually already executed.
   *
   * Reads through `builtRef` so the callback identity survives rebuilds — a new
   * identity here would reinstall the handler on every node event. */
  const resolveConnectableNodeId = useCallback(
    (renderId: string, role: "source" | "target"): string | null => {
      for (const cluster of Object.values(builtRef.current.templateInstances)) {
        if (!cluster.collapsed) continue;
        if (templateInstanceBoxNodeId(cluster.instanceId) !== renderId) continue;
        if (role === "target") return null;
        return cluster.sinkNodeIds.length === 1 ? cluster.sinkNodeIds[0] : null;
      }
      return renderId;
    },
    [],
  );
  const nodesByIdRef = useRef(new Map<string, NodeInfo>());
  nodesByIdRef.current = useMemo(
    () => new Map(nodes.map((node) => [node.id, node])),
    [nodes],
  );
  /* The wire currently being pulled out of a tile, if any. Lives in a module
   * store shared with AgentNode, which starts the gesture. */
  const wiringDrag = useWiringDrag();
  /* Which dependency edge is currently offering to be withdrawn, and whether it
   * has advanced to the confirm step. Local canvas UI state: it stays out of
   * `buildGraph`, which is a pure function pinned by the layout tests. */
  const [disconnectTarget, setDisconnectTarget] =
    useState<DisconnectTarget | null>(null);
  const disconnectTargetRef = useRef(disconnectTarget);
  disconnectTargetRef.current = disconnectTarget;

  const requestDisconnect = useCallback(() => {
    setDisconnectTarget((current) =>
      current ? { ...current, confirming: true } : current,
    );
  }, []);

  const confirmDisconnect = useCallback(() => {
    const pending = disconnectTargetRef.current;
    setDisconnectTarget(null);
    if (!pending) return;
    /* Re-check at the moment of the write: the graph may have moved on between
     * the click and the confirmation. */
    if (
      !canDisconnectDependency(
        pending.sourceId,
        pending.targetId,
        nodesByIdRef.current,
      ) ||
      !canMutateNode(pending.targetId)
    ) {
      return;
    }
    onDisconnectDependency?.(pending.targetId, pending.sourceId);
  }, [canMutateNode, onDisconnectDependency]);

  const cancelDisconnect = useCallback(() => setDisconnectTarget(null), []);

  /* Escape backs out of the withdraw gesture, matching the canvas's other
   * dismissable affordances. Registered only while one is open. */
  useEffect(() => {
    if (!disconnectTarget) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDisconnectTarget(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [disconnectTarget]);

  /* Drop the withdraw affordance once its edge is gone from the layout —
   * withdrawn here, changed in the inspector, or hidden by a collapse. Left
   * alone it would keep a control floating at the last known label position. */
  useEffect(() => {
    setDisconnectTarget((current) => {
      if (!current) return current;
      return built.rfEdges.some((edge) => edge.id === current.edgeId)
        ? current
        : null;
    });
  }, [built.rfEdges]);

  const edgeDisconnect = useMemo<EdgeDisconnectDecoration | null>(
    () =>
      disconnectTarget
        ? {
            edgeId: disconnectTarget.edgeId,
            confirming: disconnectTarget.confirming,
            onRequest: requestDisconnect,
            onConfirm: confirmDisconnect,
            onCancel: cancelDisconnect,
          }
        : null,
    [disconnectTarget, requestDisconnect, confirmDisconnect, cancelDisconnect],
  );
  /* Resolve a wire release to the durable pair it would write, or to a request
   * for a new downstream node when it landed on empty canvas. */
  const resolveDropSurface = useCallback(
    (
      clientX: number,
      clientY: number,
    ): import("./dependencyWiring").WiringDropSurface => {
      /* Walk up from whatever is under the cursor to the nearest React Flow
       * node element, the same way the template-drop path does. The wire
       * overlay itself is `pointer-events-none`, so it never shadows the hit. */
      let cursor = document.elementFromPoint(clientX, clientY) as HTMLElement | null;
      while (cursor) {
        const dataId = cursor.getAttribute?.("data-id");
        if (dataId) {
          const found = builtRef.current.rfNodes.find((n) => n.id === dataId);
          /* Lanes and group frames are containers, not endpoints: a release
           * over lane background is a release on empty canvas. */
          if (
            !found ||
            found.type === "planspaceLane" ||
            found.type === "templateGroup"
          ) {
            return found ? { kind: "canvas" } : { kind: "blocked" };
          }
          const targetId = resolveConnectableNodeId(dataId, "target");
          return targetId
            ? { kind: "target", targetId }
            : { kind: "blocked" };
        }
        if (
          cursor.classList?.contains("react-flow__pane") ||
          cursor.classList?.contains("react-flow__renderer")
        ) {
          return { kind: "canvas" };
        }
        if (cursor === wrapperRef.current) break;
        cursor = cursor.parentElement;
      }
      return { kind: "blocked" };
    },
    [resolveConnectableNodeId],
  );

  /* The wire's lifecycle. Mounted only while one is in flight, so the canvas
   * carries no pointer listeners at rest.
   *
   * Listeners go on the window rather than the canvas: the gesture starts on a
   * button inside a node and can legitimately travel outside the canvas before
   * coming back, and a release anywhere has to end it. */
  useEffect(() => {
    if (!wiringDrag) return;
    const onPointerMove = (event: PointerEvent) => {
      const surface = resolveDropSurface(event.clientX, event.clientY);
      const action = resolveWiringDrop(
        wiringDrag.sourceId,
        surface,
        nodesByIdRef.current,
      );
      /* Only a target that would actually accept the dependency lights up. */
      moveWiringPointer(
        { x: event.clientX, y: event.clientY },
        action.kind === "connect" && canMutateNode(action.targetId)
          ? action.targetId
          : null,
      );
    };
    const onPointerUp = (event: PointerEvent) => {
      const surface = resolveDropSurface(event.clientX, event.clientY);
      const action = resolveWiringDrop(
        wiringDrag.sourceId,
        surface,
        nodesByIdRef.current,
      );
      endWiringDrag();
      if (action.kind === "connect") {
        if (!canMutateNode(action.targetId)) return;
        onConnectDependency?.(action.targetId, action.sourceId);
        return;
      }
      if (action.kind === "create") {
        /* Empty canvas: a new draft virtual that waits for the source, placed
         * where the user released so the node appears under the cursor rather
         * than at the lane's default append slot. */
        const source = nodesByIdRef.current.get(action.sourceId);
        const planspaceId = source?.planspace_id ?? null;
        if (!planspaceId) return;
        const flowPosition = screenToFlowPosition({
          x: event.clientX,
          y: event.clientY,
        });
        const lane = rfNodesRef.current.find(
          (node) => node.id === `planspace:${planspaceId}`,
        );
        onCreateDependencyVirtualAt?.(
          action.sourceId,
          lane
            ? snapPlanspaceChildPosition(flowPosition, lane.position)
            : flowPosition,
        );
      }
    };
    /* Escape abandons the wire, matching the canvas's other dismissable
     * affordances. `capture` so it wins over anything else listening. */
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") endWiringDrag();
    };
    const onPointerCancel = () => endWiringDrag();
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerCancel);
    window.addEventListener("keydown", onKeyDown, true);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerCancel);
      window.removeEventListener("keydown", onKeyDown, true);
    };
  }, [
    wiringDrag,
    resolveDropSurface,
    canMutateNode,
    onConnectDependency,
    onCreateDependencyVirtualAt,
    screenToFlowPosition,
  ]);
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
  /* Publish "where would a new node go in this lane" to the App-level handlers
   * behind the lane "+" and Git Review buttons. Those run outside the canvas but
   * need its live child geometry, which drag can move away from the built
   * layout. Registered as an effect so the resolver is torn down with the
   * canvas rather than outliving it. */
  useEffect(() => {
    setLaneAppendResolver((planspaceId, anchorNodeIds, forNodeId) => {
      const laneNodeId = `planspace:${planspaceId}`;
      const liveNodes = rfNodesRef.current as RFNode[];
      const anchorNodeId = resolveRenderedLaneAnchorId(
        laneNodeId,
        liveNodes,
        anchorNodeIds,
        resolveRenderId,
      );
      return appendBelowLanePosition(
        laneNodeId,
        liveNodes,
        anchorNodeId,
        forNodeId,
      );
    });
    return () => setLaneAppendResolver(null);
  }, [resolveRenderId]);
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
    /* A commit that lands while the tree is still dirty takes the ghost's row;
     * step the ghost down so pending changes stay at the end of the trunk. */
    const displacedGhostPosition =
      commitPositionTransfer || gitChangesAppearancePosition
        ? null
        : resolveDisplacedGhostPosition(
            rfNodesRef.current as RFNode[],
            layeredBuiltNodes,
          );
    if (
      syncedBuiltNodesRef.current === layeredBuiltNodes &&
      !hydrateFromLayout &&
      !commitPositionTransfer &&
      !gitChangesAppearancePosition &&
      !displacedGhostPosition
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
    if (displacedGhostPosition) {
      layoutHintsRef.current["commit:ghost"] = displacedGhostPosition;
      pendingHintsRef.current["commit:ghost"] = displacedGhostPosition;
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
        /* Group frames are derived, never dragged. Explicit UI placement must
         * beat an existing runtime position: a websocket refresh can expose a
         * newly-created node at its default position before the create request
         * returns the double-click target to App. */
        const preserveRuntimePosition =
          !hydrateFromLayout &&
          n.type !== "templateGroup" &&
          !(
            commitPositionTransfer?.resetGhostPosition &&
            n.id === commitPositionTransfer.fromId
          );
        const syncedPosition = resolveSyncedNodePosition(
          n.position,
          runtime?.position,
          preserveRuntimePosition,
          n.id === nodePositionTarget?.nodeId
            ? nodePositionTarget.position
            : undefined,
        );
        if (
          syncedPosition.x !== out.position.x ||
          syncedPosition.y !== out.position.y
        ) {
          out = { ...out, position: syncedPosition };
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
        if (n.id === "commit:ghost" && displacedGhostPosition) {
          out = { ...out, position: displacedGhostPosition };
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
    nodePositionTarget,
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

  /* Which rendered nodes are selected right now, as a value that only changes
   * when the selection does. Derived from `rfNodes` because React Flow owns
   * selection there; a dragged tile rewrites that array on every frame, so the
   * key is what keeps the memos below from recomputing mid-drag. */
  const selectedRenderKey = useMemo(
    () =>
      (rfNodes as RFNode[])
        .filter((node) => node.selected)
        .map((node) => node.id)
        .sort()
        .join("|"),
    [rfNodes],
  );
  const selectedRenderIds = useMemo(
    () => new Set(selectedRenderKey ? selectedRenderKey.split("|") : []),
    [selectedRenderKey],
  );

  /* Arrows that may claim a pointer hit this render. Everything else is left
   * inert so right-drag panning is not interrupted by a strip of dead canvas
   * along an arrow nothing can act on — see `resolveInteractiveDependencyEdges`
   * and the `edge-interactive` rule in `index.css`. */
  const interactiveEdgeIds = useMemo(
    () =>
      resolveInteractiveDependencyEdges({
        edges: built.rfEdges,
        selectedRenderIds,
        resolveConnectableNodeId,
        canWithdraw: (sourceId, targetId) =>
          canDisconnectDependency(sourceId, targetId, nodesByIdRef.current) &&
          canMutateNode(targetId),
        keepEdgeId: disconnectTarget?.edgeId ?? null,
      }),
    [
      built.rfEdges,
      selectedRenderIds,
      resolveConnectableNodeId,
      canMutateNode,
      nodes,
      disconnectTarget?.edgeId,
    ],
  );

  /* Edges depend on hover (the `loads` lane fades in only for the hovered or
   * selected node), so they get a separate effect. */
  useEffect(() => {
    setRfEdges(
      decorateEdges(
        built.rfEdges,
        selectedNodeId,
        hoverGroup,
        edgeDisconnect,
        interactiveEdgeIds,
      ),
    );
  }, [
    built.rfEdges,
    selectedNodeId,
    hoverGroup,
    edgeDisconnect,
    interactiveEdgeIds,
    setRfEdges,
  ]);

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

  /* Right-drag panning over tiles and arrows.
   *
   * React Flow refuses to pan from anything carrying `nopan`, which it stamps on
   * every node wrapper and every edge — so without this, a right-drag that
   * begins on a tile does nothing at all. The canvas runs those presses itself,
   * over the same viewport plumbing ctrl+wheel zoom already uses. See
   * `rightDragPan.ts` for which presses qualify.
   *
   * Capture phase on the wrapper: node tiles call `stopPropagation` on mousedown
   * to protect their own controls, so a bubble-phase listener would never see
   * the press it needs to act on. */
  const rightDragRef = useRef<{
    pointerId: number;
    lastX: number;
    lastY: number;
    startX: number;
    startY: number;
    panned: boolean;
  } | null>(null);
  /* Set when a right-drag actually moved the canvas, so the release does not
   * also open a menu.
   *
   * Cleared at the start of every right-button press rather than when it is
   * consumed, because which event would consume it is platform-dependent:
   * macOS fires `contextmenu` on mousedown, before the drag is even known to be
   * one, while other platforms fire it on mouseup. Resetting on press means the
   * flag can only ever describe the gesture in progress. */
  const suppressNextContextMenuRef = useRef(false);

  const describeRightDragHit = useCallback((target: HTMLElement): RightDragHit => {
    const nodeEl = target.closest(".react-flow__node");
    return {
      insideNoPan: Boolean(target.closest(".nopan")),
      insideSelectedNode: Boolean(nodeEl?.classList.contains("selected")),
      insideSelectionRect: Boolean(
        target.closest(".react-flow__nodesselection-rect"),
      ),
      insideEditable: Boolean(
        target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true']"),
      ),
    };
  }, []);

  const onPointerDownCapture = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (event.button !== 2) return;
      suppressNextContextMenuRef.current = false;
      if (rightDragRef.current) return;
      const target = event.target as HTMLElement | null;
      if (!target) return;
      if (!shouldPanThroughRightDrag(describeRightDragHit(target))) return;
      rightDragRef.current = {
        pointerId: event.pointerId,
        lastX: event.clientX,
        lastY: event.clientY,
        startX: event.clientX,
        startY: event.clientY,
        panned: false,
      };
    },
    [describeRightDragHit],
  );

  /* Window-level move/up: the press starts over a tile, and the pointer routinely
   * leaves it mid-drag. Listening on the wrapper would drop the gesture the
   * moment it crossed onto something else. */
  useEffect(() => {
    const onMove = (event: PointerEvent) => {
      const drag = rightDragRef.current;
      if (!drag || event.pointerId !== drag.pointerId) return;
      const dx = event.clientX - drag.lastX;
      const dy = event.clientY - drag.lastY;
      drag.lastX = event.clientX;
      drag.lastY = event.clientY;
      if (
        !drag.panned &&
        !isRightDragPan(event.clientX - drag.startX, event.clientY - drag.startY)
      ) {
        return;
      }
      drag.panned = true;
      if (dx === 0 && dy === 0) return;
      const next = panViewportBy(liveViewportRef.current, dx, dy);
      liveViewportRef.current = next;
      viewportRef.current = next;
      pendingViewportRef.current = next;
      setViewport(next, { duration: 0 });
    };
    const onUp = (event: PointerEvent) => {
      const drag = rightDragRef.current;
      if (!drag || event.pointerId !== drag.pointerId) return;
      rightDragRef.current = null;
      if (!drag.panned) return;
      /* The gesture moved the canvas, so a menu event arriving on release must
       * not be read as a right-click. It matters when the press began on an
       * unselected tile and released over a selected one, whose handler would
       * otherwise open its menu at the end of a pan. */
      suppressNextContextMenuRef.current = true;
      scheduleFlushLayout(250);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [scheduleFlushLayout, setViewport]);

  /* One handler owns "no browser menu on this canvas".
   *
   * React Flow's own suppression only covers the pane, and the node handler now
   * declines unselected tiles so their presses can pan — which would otherwise
   * let the OS menu through. Suppressing here instead keeps the rule in one
   * place and independent of what the press landed on.
   *
   * Bubble phase, so the node handler above has already read
   * `suppressNextContextMenuRef` by the time this runs. The flag itself is reset
   * on the next right-button press, not here. */
  const onCanvasContextMenu = useCallback((event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
  }, []);

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
      setDisconnectTarget(null);
      pendingUserSelectionRef.current = {
        nodeId: null,
        preserveExisting: false,
      };
      onSelectionChange({ kind: "none" });
    },
    [onSelectionChange],
  );

  /* Clicking a dependency edge offers to withdraw it. Two steps, because the
   * edge's invisible hit path is 20px wide and a single click that rewrites the
   * graph would fire on a near miss. Left-click only — `onEdgeClick` is React's
   * onClick, which the right button never triggers, so this cannot interfere
   * with right-drag panning. */
  const onEdgeClick = useCallback<EdgeMouseHandler>(
    (_event, edge) => {
      /* React Flow has already deselected every node on its way here —
       * `addSelectedEdges` clears the node selection unless multi-select is
       * held. That would drop the selected tile's ring and any marquee
       * selection the user built for save-as-template, so the pre-click flags
       * are restored. Snapshotting from the ref is safe: the deselect is a
       * queued state update, so the ref still holds the pre-click nodes. */
      const selectedBefore = new Set(
        (rfNodesRef.current as RFNode[])
          .filter((n) => n.selected)
          .map((n) => n.id),
      );
      setRfNodes((current) =>
        (current as RFNode[]).map((n) => {
          const desired = selectedBefore.has(n.id);
          return n.selected === desired ? n : { ...n, selected: desired };
        }),
      );

      if (edge.type !== "dependency") {
        setDisconnectTarget(null);
        return;
      }
      const sourceId = resolveConnectableNodeId(edge.source, "source");
      const targetId = resolveConnectableNodeId(edge.target, "target");
      if (
        !sourceId ||
        !targetId ||
        !canDisconnectDependency(sourceId, targetId, nodesByIdRef.current) ||
        !canMutateNode(targetId)
      ) {
        setDisconnectTarget(null);
        return;
      }
      setDisconnectTarget((current) =>
        current?.edgeId === edge.id
          ? current
          : { edgeId: edge.id, sourceId, targetId, confirming: false },
      );
    },
    [canMutateNode, resolveConnectableNodeId, setRfNodes],
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

  /* A wire cannot outlive the canvas that draws it. Switching projects unmounts
   * mid-gesture if the user is holding one, and the store is a module singleton
   * that would otherwise hand the next canvas a stale drag to render. */
  useEffect(() => () => endWiringDrag(), []);

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
      const rf = node as RFNode;
      /* An unselected tile has no menu, so the press is left to fall through to
       * d3-zoom and pan like empty canvas would. Returning without
       * `preventDefault` is the whole point: the browser menu is suppressed by
       * the wrapper's own contextmenu handler, which keeps right-drag usable
       * over tiles the user has not selected. */
      if (!rf.selected) return;
      event.preventDefault();
      /* A right-drag that panned across this tile and released over it must not
       * also open its menu. */
      if (suppressNextContextMenuRef.current) return;
      let agentId: string | null = null;
      if (rf.type === "agent") {
        const data = rf.data as import("./layout").AgentNodeData;
        agentId = data.node.id;
      }
      onAgentNodeContextMenu?.(agentId, event.clientX, event.clientY);
    },
    [onAgentNodeContextMenu],
  );

  /* The marquee rect React Flow floats over a multi-selection sits above the
   * tiles, so it — not they — receives the right-click that should offer to save
   * the whole selection as a template. Without this the rect would swallow the
   * gesture, and because it also carries `nopan` the press could neither open a
   * menu nor pan. The tile handler's guards do not apply: the rect only exists
   * while a selection does. */
  const onSelectionContextMenu = useCallback(
    (event: React.MouseEvent, selNodes: Node[]) => {
      event.preventDefault();
      if (suppressNextContextMenuRef.current) return;
      const firstAgent = (selNodes as RFNode[]).find(
        (node) => node.type === "agent",
      );
      const agentId = firstAgent
        ? (firstAgent.data as import("./layout").AgentNodeData).node.id
        : null;
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
      className="group/canvas relative h-full w-full"
      onWheelCapture={onWheelCapture}
      onPointerDownCapture={onPointerDownCapture}
      onContextMenu={onCanvasContextMenu}
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
        onSelectionContextMenu={onSelectionContextMenu}
        onNodeMouseEnter={onNodeMouseEnter}
        onNodeMouseLeave={onNodeMouseLeave}
        onEdgeClick={onEdgeClick}
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
        <CenterOnNode
          request={centerOnNodeRequest ?? null}
          resolveRenderId={resolveRenderId}
        />
        <Controls
          className="!border !border-line !bg-surface-raised !shadow-card"
          showInteractive={false}
          onZoomIn={persistCurrentViewport}
          onZoomOut={persistCurrentViewport}
          onFitView={persistCurrentViewport}
        />
      </ReactFlow>
      <LaneVerticalJumpControls
        activePlanspaceId={activePlanspaceId}
        wrapperRef={wrapperRef}
        liveViewportRef={liveViewportRef}
        nodesRef={rfNodesRef as React.MutableRefObject<RFNode[]>}
        nodesVersion={rfNodes}
      />
      {/* Drawn over React Flow rather than inside its viewport: the wire is in
        * screen coordinates, so it needs no transform and survives a pan
        * mid-gesture without re-running layout. */}
      {wiringDrag && <WiringOverlay drag={wiringDrag} />}
    </div>
  );
}

/* Bring a node into view when something outside the canvas selects it.
 *
 * Selection alone only draws a highlight ring; a node scrolled off-screen
 * gives the user no visible feedback at all. Driven by a version counter
 * rather than the node id so selecting the same node twice still recenters.
 *
 * The viewport change is intentionally not persisted: `onMoveEnd` ignores
 * moves with no originating user event, which keeps saved viewports owned by
 * the user.
 */
function CenterOnNode({
  request,
  resolveRenderId,
}: {
  request: CanvasCenterRequest | null;
  resolveRenderId: (nodeId: string) => string;
}) {
  const { getNode, getViewport, setCenter } = useReactFlow();
  const handledVersionRef = useRef(0);
  useEffect(() => {
    if (!request || request.version === 0) return;
    if (handledVersionRef.current === request.version) return;
    /* The node may not be in the React Flow store yet: a cross-project jump
     * mounts the canvas and issues this request in the same commit, and lane
     * layout runs after that. Retry a few frames before giving up, otherwise
     * a jump that arrives marginally early silently does nothing. */
    let attempts = 0;
    let timer = 0;
    const attempt = () => {
      const node = getNode(resolveRenderId(request.nodeId));
      if (!node) {
        attempts += 1;
        if (attempts > 10) return;
        timer = window.setTimeout(attempt, 60);
        return;
      }
      handledVersionRef.current = request.version;
      const width = node.width ?? 0;
      const height = node.height ?? 0;
      const position = node.positionAbsolute ?? node.position;
      /* setCenter falls back to maxZoom when `zoom` is omitted, which would
       * slam the canvas to 2x on every jump. Panning must not change zoom. */
      setCenter(position.x + width / 2, position.y + height / 2, {
        duration: 200,
        zoom: getViewport().zoom,
      });
    };
    timer = window.setTimeout(attempt, 60);
    return () => window.clearTimeout(timer);
  }, [request, getNode, getViewport, resolveRenderId, setCenter]);
  return null;
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

/* Jump to the top or bottom of the active lane.
 *
 * A lane grows downward as work is appended, and the tall ones reach well past
 * ten thousand flow pixels — far enough that panning back to the header is a
 * chore and `fitView` is not a substitute, since it reframes every lane at a
 * zoom where nothing is readable. These two buttons pan vertically only,
 * holding X and zoom, so the branch the user was reading stays in frame.
 *
 * Fixed to the canvas viewport rather than drawn inside the lane: a button
 * anchored to a twelve-thousand-pixel-tall lane's own corner is itself off
 * screen exactly when it is needed. Bottom-left, beside React Flow's own
 * `Controls`: the bottom-right belongs to the node details panel, which is
 * 380px wide at z-20 and would bury these whenever it is open.
 *
 * Only mounted for the active lane. It is where new work lands, and "current
 * lane" cannot be derived from a viewport-fixed button's position — a pointer
 * hovering the canvas may be over any lane, or none.
 */
function LaneVerticalJumpControls({
  activePlanspaceId,
  wrapperRef,
  liveViewportRef,
  nodesRef,
  nodesVersion,
}: {
  activePlanspaceId: string | null;
  wrapperRef: React.RefObject<HTMLDivElement>;
  liveViewportRef: React.MutableRefObject<Viewport>;
  nodesRef: React.MutableRefObject<RFNode[]>;
  /** Bumped whenever lane geometry may have changed, so growth past the
   * two-screen threshold is noticed without polling the node array. */
  nodesVersion: unknown;
}) {
  const { setCenter, getViewport } = useReactFlow();
  const store = useStoreApi();
  const [visible, setVisible] = useState(false);
  const [leftOffset, setLeftOffset] = useState(() =>
    resolveLaneJumpLeftOffset(null),
  );
  const [jumps, setJumps] = useState({
    canJumpToTop: false,
    canJumpToBottom: false,
  });
  const spanRef = useRef<LaneVerticalSpan | null>(null);

  const measure = useCallback(() => {
    const wrapper = wrapperRef.current;
    const span = resolveLaneVerticalSpan(nodesRef.current, activePlanspaceId);
    spanRef.current = span;
    if (!wrapper || !span) {
      setVisible(false);
      return;
    }
    const height = wrapper.clientHeight;
    const viewport = liveViewportRef.current;
    const nextVisible = laneNeedsVerticalJump(span, height, viewport.zoom);
    setVisible(nextVisible);
    if (!nextVisible) return;
    /* Re-measure the Controls column here rather than once on mount: it renders
     * a frame after the canvas (it self-gates on an effect) and its width
     * changes with the button count. */
    setLeftOffset(
      resolveLaneJumpLeftOffset(
        wrapper.querySelector<HTMLElement>(".react-flow__controls"),
      ),
    );
    /* Flow coordinates of the visible band: screen 0..height mapped back
     * through the viewport transform. */
    const flowTop = -viewport.y / viewport.zoom;
    setJumps(
      availableLaneJumps(span, flowTop, flowTop + height / viewport.zoom),
    );
  }, [activePlanspaceId, liveViewportRef, nodesRef, wrapperRef]);

  /* One debounced recompute drives both the visibility test and the two
   * enabled states. Viewport transforms fire continuously during pans, zooms,
   * and animations, so measuring on every event would re-render the overlay
   * throughout a move. A trailing timer collapses each gesture into a single
   * measurement after it settles. */
  const timerRef = useRef<number | null>(null);
  const schedule = useCallback(
    (delayMs = 120) => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        measure();
      }, delayMs);
    },
    [measure],
  );

  /* Subscribe to React Flow's transform store instead of DOM gestures. This
   * covers every viewport path, including `fitView`, `setCenter`, and Controls
   * actions whose animated moves have no originating pointer/wheel event. The
   * subscription also keeps the shared live ref current without re-rendering
   * this component on every animation frame. */
  useEffect(() => {
    const syncViewport = (transform: readonly [number, number, number]) => {
      const [x, y, zoom] = transform;
      liveViewportRef.current = { x, y, zoom };
      schedule();
    };
    syncViewport(store.getState().transform);
    return store.subscribe((state, previousState) => {
      const [x, y, zoom] = state.transform;
      const [previousX, previousY, previousZoom] = previousState.transform;
      if (x === previousX && y === previousY && zoom === previousZoom) return;
      syncViewport(state.transform);
    });
  }, [liveViewportRef, schedule, store]);

  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    const onResize = () => schedule();
    const observer = new ResizeObserver(onResize);
    observer.observe(wrapper);
    return () => {
      observer.disconnect();
    };
  }, [schedule, wrapperRef]);

  useEffect(() => {
    schedule();
  }, [schedule, nodesVersion]);

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  const jumpTo = useCallback(
    (edge: "top" | "bottom") => {
      const span = spanRef.current;
      const wrapper = wrapperRef.current;
      if (!span || !wrapper) return;
      const viewport = getViewport();
      const halfFlowHeight = wrapper.clientHeight / viewport.zoom / 2;
      /* Hold X and zoom: this is a vertical scroll, not a reframe. Offsetting
       * by half the visible height puts the lane edge at the top (or bottom)
       * of the viewport rather than in its middle, which is what "back to the
       * top" means to the user. */
      const centerX = (-viewport.x + wrapper.clientWidth / 2) / viewport.zoom;
      const targetY =
        edge === "top"
          ? span.topY + halfFlowHeight
          : span.bottomY - halfFlowHeight;
      setCenter(centerX, targetY, { zoom: viewport.zoom, duration: 320 });
    },
    [getViewport, setCenter, wrapperRef],
  );

  if (!visible) return null;

  return (
    <div
      /* `pointer-events-none` on the column with `auto` on the buttons keeps
       * the gap between them from swallowing pans over the canvas.
       *
       * Placed just right of React Flow's `Controls` and sharing its 15px
       * bottom margin, so the two read as one cluster without covering the
       * zoom / fit-view buttons. The offset is measured from that column, not
       * assumed, since its width comes from React Flow's own stylesheet. */
      className="pointer-events-none absolute bottom-[15px] z-10 flex flex-col gap-1.5 opacity-0 transition-opacity duration-150 focus-within:opacity-100 group-hover/canvas:opacity-100"
      style={{ left: leftOffset }}
    >
      <LaneJumpButton
        label="回到本方向顶部"
        disabled={!jumps.canJumpToTop}
        onClick={() => jumpTo("top")}
      >
        ↑
      </LaneJumpButton>
      <LaneJumpButton
        label="到本方向底部"
        disabled={!jumps.canJumpToBottom}
        onClick={() => jumpTo("bottom")}
      >
        ↓
      </LaneJumpButton>
    </div>
  );
}

function LaneJumpButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      /* Clickable only once actually shown. Keyboard focus is unaffected by
       * `pointer-events`, so the focus-within reveal on the column still works;
       * this only stops a pointer from hitting a fully transparent button. */
      className="nodrag pointer-events-none inline-flex h-7 w-7 items-center justify-center rounded border border-line bg-surface-raised/95 text-[13px] leading-none text-ink-muted shadow-card transition hover:border-line-strong hover:text-ink-strong focus-visible:pointer-events-auto disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:border-line disabled:hover:text-ink-muted group-hover/canvas:pointer-events-auto"
    >
      {children}
    </button>
  );
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
