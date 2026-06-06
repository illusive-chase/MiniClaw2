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
  type OnSelectionChangeFunc,
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
import { ArtifactNode } from "./nodes/ArtifactNode";
import { ContextNode } from "./nodes/ContextNode";
import { PhantomNode } from "./nodes/PhantomNode";
import { ProjectRootNode } from "./nodes/ProjectRootNode";
import {
  LoadsEdge,
  ProducesEdge,
  ResumeEdge,
  ReviewsEdge,
  TimelineEdge,
} from "./edges/TimelineEdge";

const NODE_TYPES = {
  agent: AgentNode,
  gate: GateNode,
  op: OpNode,
  artifact: ArtifactNode,
  context: ContextNode,
  phantom: PhantomNode,
  projectRoot: ProjectRootNode,
};

const EDGE_TYPES = {
  timeline: TimelineEdge,
  resume: ResumeEdge,
  produces: ProducesEdge,
  reviews: ReviewsEdge,
  loads: LoadsEdge,
};

export type CanvasSelection =
  | { kind: "agent" | "gate" | "op"; nodeId: string }
  | { kind: "artifact"; ownerNodeId: string; path: string; artifactKind: string }
  | { kind: "context"; identityKey: string; path: string }
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
  onSelectionChange: (sel: CanvasSelection) => void;
  onEmptyCanvasTap: (position: { x: number; y: number }) => void;
  /** spawn the phantom to the right of a finished agent */
  onSpawnFromAgent: (nodeId: string) => void;
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
  onSelectionChange,
  onEmptyCanvasTap,
  onSpawnFromAgent,
}: CanvasProps) {
  const layoutHintsRef = useRef<Record<string, { x: number; y: number }>>({});
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);

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
      }),
    [
      nodes,
      activeNodeId,
      projectTitle,
      phantomFromNodeId,
      phantomDisabled,
      contextBundlesByNodeId,
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

  /* Sync upstream changes into local state without trampling drag positions. */
  useEffect(() => {
    setRfNodes((current) => {
      const positionById = new Map(current.map((n) => [n.id, n.position]));
      return decorateSelection(
        built.rfNodes.map((n) => {
          const existing = positionById.get(n.id);
          return existing ? { ...n, position: existing } : n;
        }),
        selectedNodeId,
      );
    });
    setRfEdges(decorateEdges(built.rfEdges, selectedNodeId, hoveredNodeId));
  }, [built.rfNodes, built.rfEdges, selectedNodeId, hoveredNodeId, setRfNodes, setRfEdges]);

  /* Persist drag positions client-side so they survive node updates. */
  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      for (const ch of changes) {
        if (ch.type === "position" && ch.position && ch.dragging === false) {
          layoutHintsRef.current[ch.id] = ch.position;
        }
      }
      onNodesChange(changes);
    },
    [onNodesChange],
  );

  /* Click → selection. We translate React Flow nodes back into the parent's
   * polymorphic CanvasSelection shape. */
  const onSelChange = useCallback<OnSelectionChangeFunc>(
    ({ nodes: selNodes }) => {
      const n = selNodes[0] as RFNode | undefined;
      if (!n) {
        onSelectionChange({ kind: "none" });
        return;
      }
      if (n.type === "agent") {
        const data = n.data as import("./layout").AgentNodeData;
        onSelectionChange({ kind: "agent", nodeId: data.node.id });
      } else if (n.type === "gate") {
        const data = n.data as import("./layout").GateNodeData;
        onSelectionChange({ kind: "gate", nodeId: data.node.id });
      } else if (n.type === "op") {
        const data = n.data as import("./layout").OpNodeData;
        onSelectionChange({ kind: "op", nodeId: data.node.id });
      } else if (n.type === "artifact") {
        const data = n.data as import("./layout").ArtifactNodeData;
        onSelectionChange({
          kind: "artifact",
          ownerNodeId: data.ownerNodeId,
          path: data.path,
          artifactKind: data.artifactKind,
        });
      } else if (n.type === "context") {
        const data = n.data as import("./layout").ContextNodeData;
        onSelectionChange({
          kind: "context",
          identityKey: data.identityKey,
          path: data.path,
        });
      } else if (n.type === "projectRoot") {
        onSelectionChange({ kind: "projectRoot" });
      } else {
        onSelectionChange({ kind: "none" });
      }
    },
    [onSelectionChange],
  );

  /* Empty-canvas tap: spawn a fresh-start phantom at the click position. */
  const onPaneClick = useCallback(
    (event: React.MouseEvent) => {
      const target = event.target as HTMLElement;
      const isPane =
        target.classList.contains("react-flow__pane") ||
        target.classList.contains("react-flow__renderer");
      if (!isPane) return;
      const rect = (event.currentTarget as HTMLDivElement).getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      onEmptyCanvasTap({ x, y });
    },
    [onEmptyCanvasTap],
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
        onSelectionChange={onSelChange}
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
        selectionOnDrag
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

/* Floating label so users know what the top strip means. */
function ContextLaneLabel() {
  return (
    <div className="pointer-events-none absolute left-4 top-2 z-10 inline-flex items-center gap-1.5 rounded border border-line bg-surface-raised/85 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle backdrop-blur">
      <span className="inline-block h-1 w-1 rounded-full bg-ink-subtle" />
      Context lane
    </div>
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

function decorateSelection(nodes: RFNode[], selectedNodeId: string | null): Node[] {
  if (!selectedNodeId) return nodes;
  return nodes.map((n) =>
    n.id === selectedNodeId ? { ...n, selected: true } : n,
  );
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
    if (selectedNodeId && (e.source === selectedNodeId || e.target === selectedNodeId)) {
      return { ...e, selected: true };
    }
    return e;
  });
}
