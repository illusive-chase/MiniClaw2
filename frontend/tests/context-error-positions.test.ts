import assert from "node:assert/strict";
import { updateContextLayout } from "../src/api";
import type { ContextBundleSources, NodeInfo, NodePosition } from "../src/types";
import { buildGraph, contextIdentityKey, type BuildGraphArgs } from "../src/canvas/layout";
import { contextPositionUpdate, filterContextPositions } from "../src/canvas/contextPositions";
import { filterNodePositions, nodePositionUpdate } from "../src/canvas/nodePositions";

const owner: NodeInfo = {
  id: "owner", project_id: "project", kind: "agent", state: "error", error: "测试失败",
  provider: null, prompt: "测试", created_at: 1, planspace_id: "lane",
};
const foreign: NodeInfo = { ...owner, id: "foreign", parent_node_id: owner.id, planspace_id: null };
const sources: ContextBundleSources = { sources: [
  { scope: "project-root", kind: "context", path: "CONTEXT.md", sha256: "hash", chars: 20, injection: "system" },
  { scope: "contextspace", kind: "planspace", path: "planspaces/lane/CONTEXT.md", plug_id: "lane", sha256: "hash", chars: 20, injection: "system" },
  { scope: "contextspace", kind: "principle", path: "principles/test/CONTEXT.md", sha256: "hash", chars: 20, injection: "system" },
] };
const args: BuildGraphArgs = {
  nodes: [owner, foreign], activeNodeIds: [], nodePositions: {},
  canMutateNode: (nodeId) => nodeId === owner.id, canMutateContextLayout: true,
  contextBundlesByNodeId: { [owner.id]: sources, [foreign.id]: sources },
  knownPlanspaceIds: ["lane"], hiddenPlanspaceIds: [], focusedPlanspaceId: "lane",
  autoPlanspaceIds: [], canCreateVirtual: true,
};
const original = buildGraph(args);
const contextTiles = original.rfNodes.filter((node) => node.type === "context");
assert.equal(contextTiles.length, 3);
assert.ok(contextTiles.every((node) => node.draggable));
const savedContexts: Record<string, NodePosition> = {};
for (const [index, tile] of contextTiles.entries()) {
  const position = { x: 750 + index * 250, y: 800 + index * 100 };
  const saved = contextPositionUpdate(tile, position, true)!;
  assert.ok(saved);
  assert.equal(saved.space, tile.parentNode ?? "canvas");
  assert.equal(contextPositionUpdate(tile, position, false), null);
  assert.equal(contextPositionUpdate(tile, { x: Infinity, y: 0 }, true), null);
  savedContexts[tile.id] = saved;
}
assert.equal(contextPositionUpdate(undefined, { x: 1, y: 2 }, true), null);
assert.equal(contextPositionUpdate({ id: "ctx:bad", type: "context" }, { x: 1, y: 2 }, true), null);
assert.deepEqual(filterContextPositions({
  ...savedContexts, "err:owner": { x: 1, y: 2, space: "canvas" },
  "ctx:scope::kind::bad": { x: NaN, y: 2, space: "canvas" },
  "ctx:scope::kind::wrong": { x: 1, y: 2, space: "planspace:" },
}), savedContexts);

const terminalId = `err:${owner.id}`;
const savedError: NodePosition = { x: 1900, y: 1200, space: "planspace:lane" };
const savedNodes = { [terminalId]: savedError };
assert.equal(original.rfNodes.find((node) => node.id === terminalId)?.draggable, true);
assert.equal(original.rfNodes.find((node) => node.id === `err:${foreign.id}`)?.draggable, false);
assert.deepEqual(nodePositionUpdate(args.nodes, terminalId, savedError, args.canMutateNode!), savedError);
assert.equal(nodePositionUpdate(args.nodes, `err:${foreign.id}`, savedError, args.canMutateNode!), null);
assert.deepEqual(filterNodePositions(args.nodes, savedNodes), savedNodes);
const hydrated = buildGraph({ ...args, nodePositions: savedNodes, contextPositions: savedContexts });
assert.deepEqual(hydrated.rfEdges, original.rfEdges);
for (const [nodeId, position] of Object.entries({ ...savedContexts, ...savedNodes })) {
  const tile = hydrated.rfNodes.find((node) => node.id === nodeId)!;
  assert.equal(tile.position.x, position.x);
  assert.equal(tile.position.y, position.y);
  assert.equal(tile.parentNode ?? "canvas", position.space);
}
const lane = hydrated.rfNodes.find((node) => node.id === "planspace:lane")!;
assert.ok(lane.width! >= savedError.x + 180);
assert.ok(lane.height! >= savedError.y + 88);
const moved = buildGraph({
  ...args, contextPositions: savedContexts, nodePositions: { ...savedNodes, [owner.id]: { x: 300, y: 400 } },
  lanePositions: { "planspace:lane": { x: -500, y: -600 } },
});
for (const tile of hydrated.rfNodes.filter((node) => node.type === "context" || node.id === terminalId)) {
  assert.deepEqual(moved.rfNodes.find((node) => node.id === tile.id)?.position, tile.position);
}
assert.ok(buildGraph({ ...args, canMutateNode: () => false, canMutateContextLayout: false })
  .rfNodes.every((node) => node.draggable === false));

const laneContextId = `ctx:${contextIdentityKey("contextspace", "planspace", "planspaces/lane/CONTEXT.md")}`;
const wrongSpace = buildGraph({ ...args, contextPositions: {
  [laneContextId]: { ...savedContexts[laneContextId], space: "canvas" },
} });
assert.deepEqual(wrongSpace.rfNodes.find((node) => node.id === laneContextId)?.position,
  original.rfNodes.find((node) => node.id === laneContextId)?.position);
const unbound = buildGraph({ ...args, contextBundlesByNodeId: {}, contextPositions: savedContexts });
assert.ok(!unbound.rfNodes.some((node) => node.type === "context"));
const rebound = buildGraph({ ...args, nodes: [foreign], contextBundlesByNodeId: { [foreign.id]: sources }, contextPositions: savedContexts });
for (const [nodeId, position] of Object.entries(savedContexts)) {
  const tile = rebound.rfNodes.find((node) => node.id === nodeId)!;
  assert.deepEqual(tile.position, { x: position.x, y: position.y });
}
for (const changed of [
  { ...owner, state: "done" as const }, { ...owner, error: "" },
  { ...owner, kind: "op" as const }, { ...owner, planspace_id: "other" },
]) {
  assert.deepEqual(filterNodePositions([changed], savedNodes), {});
}
assert.deepEqual(filterNodePositions([owner], savedNodes), savedNodes);
assert.equal(buildGraph({ ...args, nodes: [{ ...owner, state: "done" }] }).rfNodes.some((node) => node.id === terminalId), false);

const originalFetch = globalThis.fetch;
try {
  globalThis.fetch = async (input, init) => {
    assert.equal(input, "/sessions/project/context-layout");
    assert.equal(init?.method, "PATCH");
    assert.equal(init?.keepalive, true);
    assert.deepEqual(JSON.parse(init?.body as string), { updates: savedContexts, remove: [] });
    return new Response(JSON.stringify({ id: "project", context_positions: savedContexts }));
  };
  assert.deepEqual((await updateContextLayout("project", savedContexts)).context_positions, savedContexts);
} finally {
  globalThis.fetch = originalFetch;
}
console.log("context 与错误卡片坐标回归测试通过");
