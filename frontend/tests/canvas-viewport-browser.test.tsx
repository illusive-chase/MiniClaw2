import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Canvas, type CanvasProps } from "../src/canvas/Canvas";
import { buildGraph } from "../src/canvas/layout";
import { saveCanvasViewport } from "../src/canvas/viewportStorage";
import { setAgentNodeContext } from "../src/canvas/nodes/AgentNode";
import type { ResolveGatePayload } from "../src/components/PendingGateInline";
import { largeGraph } from "./fixtures/largeGraph";
import type { InteractionRequest, NodeInfo } from "../src/types";

const fixture = largeGraph();
const source: NodeInfo = {
  id: "source", project_id: "scale", kind: "agent", state: "done", provider: null,
  prompt: "连线来源", created_at: 1, planspace_id: "interaction",
};
const target: NodeInfo = {
  ...source, id: "target", state: "virtual", prompt: "连线目标", created_at: 2,
  scheduled_deps: ["source"],
};
const dropTarget: NodeInfo = { ...target, id: "drop", scheduled_deps: [] };
const args = {
  ...fixture, nodes: [source, dropTarget, target, ...fixture.nodes],
  knownPlanspaceIds: ["interaction", ...fixture.knownPlanspaceIds],
  focusedPlanspaceId: "interaction", canMutateNode: () => true, canCreateVirtual: true,
  nodePositions: {
    source: { x: 400, y: 200, space: "planspace:interaction" },
    drop: { x: 720, y: 200, space: "planspace:interaction" },
    target: { x: 2400, y: 200, space: "planspace:interaction" },
  },
};
const built = buildGraph(args);
const lane = built.rfNodes.find((node) => node.id === "planspace:interaction")!;
const sourceNode = built.rfNodes.find((node) => node.id === "source")!;
const targetNode = built.rfNodes.find((node) => node.id === "target")!;
const viewport = { x: -lane.position.x - 900, y: -lane.position.y, zoom: 1 };
saveCanvasViewport("viewport-test", viewport, localStorage, true);
const results = {
  selections: [] as string[][], connections: [] as string[][], menus: 0,
  responses: [] as { id: string; payload: ResolveGatePayload }[],
};
const gateRequests: Record<"ask_user" | "permission", InteractionRequest> = {
  ask_user: {
    type: "interaction_request", id: "ask-gate", node_id: "source",
    interaction_type: "ask_user", tool_name: "request_user_input", suggestions: [],
    tool_input: {
      questions: [
        { id: "choice", question: "请选择方案", options: [{ label: "保留选项" }] },
        { id: "answer", question: "请补充说明", options: null },
      ],
    },
  },
  permission: {
    type: "interaction_request", id: "permission-gate", node_id: "source",
    interaction_type: "permission", tool_name: "Bash", suggestions: [],
    tool_input: { command: "pwd" },
  },
};
let pendingGate: InteractionRequest | null = null;
const initialProps: CanvasProps = {
  ...args, sessionId: "viewport-test", selectedNodeId: null,
  initialNodePositions: args.nodePositions,
  onSelectionChange: () => {}, onMultiSelectionChange: (ids) => results.selections.push(ids),
  onAgentNodeContextMenu: () => { results.menus += 1; },
  onConnectDependency: (targetId, sourceId) => { results.connections.push([targetId, sourceId]); },
};
setAgentNodeContext({
  canMutateNode: () => true, canCreateVirtual: true, canAcceptDependency: () => true,
  onPromoteVirtual: () => {}, onDequeueNode: () => {}, onCreateContinuationVirtual: () => {},
  onCreateDependencyVirtual: () => {}, onMarkVirtualObsolete: async () => {},
  onDeleteVirtual: async () => {}, onInterruptNode: () => {}, onRerunNode: () => {},
  canPromoteVirtual: true, canDequeue: false, isManualPlanspace: () => true,
  canInterrupt: false, canRerun: false,
  pendingGateForNode: (nodeId) => pendingGate?.node_id === nodeId ? pendingGate : null,
  onResolveGate: (id, payload) => { results.responses.push({ id, payload }); }, modelPresets: [],
});

function Harness() {
  const [props, setProps] = useState(initialProps);
  const [mountVersion, setMountVersion] = useState(0);
  Object.assign(window, {
    canvasTest: {
      results, total: built.rfNodes.length, viewport, nodes: args.nodes,
      crossingEdges: built.rfEdges.filter((edge) => edge.source === "source"),
      source: { x: lane.position.x + sourceNode.position.x, y: lane.position.y + sourceNode.position.y },
      target: { x: lane.position.x + targetNode.position.x, y: lane.position.y + targetNode.position.y },
      patch: (patch: Partial<CanvasProps>) => setProps((current) => ({ ...current, ...patch })),
      openGate: (kind: keyof typeof gateRequests) => {
        pendingGate = gateRequests[kind];
        setProps((current) => ({
          ...current, pendingGateNodeIds: ["source"],
          nodes: current.nodes.map((node) => node.id === "source" ? { ...node, state: "waiting" } : node),
        }));
      },
      closeGate: () => {
        pendingGate = null;
        setProps((current) => ({
          ...current, pendingGateNodeIds: [],
          nodes: current.nodes.map((node) => node.id === "source" ? { ...node, state: "done" } : node),
        }));
      },
      remount: () => {
        setProps((current) => ({ ...current, centerOnNodeRequest: null }));
        setMountVersion((current) => current + 1);
      },
    },
  });
  return <Canvas key={mountVersion} {...props} />;
}

createRoot(document.getElementById("root")!).render(<Harness />);
