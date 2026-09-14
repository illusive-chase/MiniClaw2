import assert from "node:assert/strict";
import { internalsSymbol, type Node } from "reactflow";
import { buildGraph } from "../src/canvas/layout";
import { createHandleBoundsSeeder, layoutHandleBounds } from "../src/canvas/viewportHandles";
import { toNodeInfo } from "../src/nodeProjection";
import { largeGraph } from "./fixtures/largeGraph";

const fixture = largeGraph();
assert.equal(fixture.nodes.length, 2055);
const ids = new Set(fixture.nodes.map((node) => node.id));
for (const node of fixture.nodes) {
  if (node.parent_node_id) assert.ok(ids.has(node.parent_node_id));
  for (const dependency of node.scheduled_deps ?? []) assert.ok(ids.has(dependency));
}
const graph = buildGraph(fixture);
assert.deepEqual(graph, buildGraph({
  ...fixture, nodes: fixture.nodes.map(toNodeInfo),
  contextBundlesByNodeId: Object.fromEntries(Object.entries(fixture.contextBundlesByNodeId)
    .map(([id, bundle]) => [id, { sources: bundle.sources, active_planspace: bundle.active_planspace }])),
}));
for (const node of graph.rfNodes) {
  assert.ok(Number.isFinite(node.width) && node.width! > 0, node.id);
  assert.ok(Number.isFinite(node.height) && node.height! > 0, node.id);
}
const seed = createHandleBoundsSeeder();
const input = new Map<string, Node>(graph.rfNodes.map((node) => [node.id, node]));
const seeded = seed(input);
assert.notEqual(seeded, input);
assert.equal(seed(seeded), seeded);
for (const edge of graph.rfEdges) {
  for (const role of ["source", "target"] as const) {
    const node = seeded.get(edge[role])!;
    const bounds = node[internalsSymbol]?.handleBounds?.[role];
    assert.ok(bounds?.length, `${edge.id}: ${role}`);
    const handleId = edge[role === "source" ? "sourceHandle" : "targetHandle"];
    if (handleId) assert.ok(bounds.some((handle) => handle.id === handleId), edge.id);
  }
}
const base: Node = { id: "agent", type: "agent", position: { x: 0, y: 0 }, width: 224, height: 100, data: {} };
const single = seed(new Map([[base.id, base]]));
assert.equal(base[internalsSymbol], undefined);
const original = single.get(base.id)!;
const resized = seed(new Map([[base.id, { ...original, height: 150 }]]));
assert.notEqual(resized.get(base.id)![internalsSymbol]?.handleBounds, original[internalsSymbol]?.handleBounds);
const measuredBounds = layoutHandleBounds({ ...base, height: 120 })!;
const measured = new Map([[base.id, { ...original, [internalsSymbol]: { handleBounds: measuredBounds, z: 10 } }]]);
assert.equal(seed(measured), measured, "不能覆盖浏览器已经测量的锚点");
for (const type of ["agent", "op", "context", "artifact", "commit", "templateInstanceBox", "templatePort", "errorTerminal"]) {
  assert.ok(layoutHandleBounds({ ...base, type }), type);
}
assert.equal(layoutHandleBounds({ ...base, width: 0 }), null);
assert.equal(layoutHandleBounds({ ...base, type: "planspaceLane" }), null);
assert.equal(layoutHandleBounds({ ...base, type: "templateGroup" }), null);
const agentBounds = layoutHandleBounds(base)!;
assert.deepEqual(agentBounds.source?.map((handle) => handle.id), [null, "produces", "epochOut"]);
assert.deepEqual(agentBounds.target?.map((handle) => handle.id), [null, "loads", "epochIn"]);
console.log("视口规模回归通过：2055 节点投影一致、尺寸完整、所有边锚点可解析、补全幂等且保留 DOM 测量");
