import assert from "node:assert/strict";
import type { ArtifactRef, NodeInfo, NodePosition } from "../src/types";
import { artifactNodeId, artifactOverflowNodeId, buildGraph, type BuildGraphArgs } from "../src/canvas/layout";
import { filterNodePositions, nodePositionUpdate } from "../src/canvas/nodePositions";

const artifacts: ArtifactRef[] = ["设计 /draft:100%?#.svg", "report.md", "page.html", "data.json", "last.md"]
  .map((name) => ({ name, bytes: 42, mtime: 1, sha256: "hash", status: "published" }));
const owner = {
  id: "owner", kind: "agent", state: "done", created_at: 1,
  planspace_id: null, settings_snapshot: { active_planspace_id: "history" }, artifacts,
} as unknown as NodeInfo;
const foreign = { ...owner, id: "foreign", parent_node_id: owner.id, settings_snapshot: {} };
const nodes = [owner, foreign];
const canMutate = (nodeId: string) => nodeId === owner.id;
const tileId = artifactNodeId(owner.id, artifacts[0].name);
const overflowId = artifactOverflowNodeId(owner.id);
const position: NodePosition = { x: 830, y: 760, space: "planspace:history" };
const overflowPosition: NodePosition = { x: 1120, y: 980, space: "planspace:history" };
const args: BuildGraphArgs = {
  nodes, activeNodeIds: [], nodePositions: {}, canMutateNode: canMutate,
  contextBundlesByNodeId: {}, knownPlanspaceIds: ["history"], hiddenPlanspaceIds: [],
  focusedPlanspaceId: "history", autoPlanspaceIds: [], canCreateVirtual: true,
};
const original = buildGraph(args);
assert.equal(original.rfNodes.find((node) => node.id === tileId)?.draggable, true);
assert.equal(original.rfNodes.find((node) => node.id === overflowId)?.draggable, true);
assert.ok(original.rfNodes.filter((node) => node.type === "artifact" && node.data.ownerNodeId === foreign.id)
  .every((node) => node.draggable === false));
assert.ok(buildGraph({ ...args, canMutateNode: () => false }).rfNodes.every((node) => node.draggable === false));

const saved = { [tileId]: position, [overflowId]: overflowPosition };
assert.deepEqual(nodePositionUpdate(nodes, tileId, position, canMutate), position);
assert.deepEqual(nodePositionUpdate(nodes, overflowId, overflowPosition, canMutate), overflowPosition);
assert.deepEqual(nodePositionUpdate(nodes, artifactNodeId(foreign.id, artifacts[0].name), position, () => true), position);
assert.deepEqual(filterNodePositions(nodes, saved), saved);
for (const invalidId of [artifactNodeId(foreign.id, artifacts[0].name), artifactNodeId(owner.id, "missing.md"), "artifact:missing:report.md"]) {
  assert.equal(nodePositionUpdate(nodes, invalidId, position, canMutate), null);
}
assert.equal(nodePositionUpdate(nodes, tileId, position, () => false), null);
assert.equal(nodePositionUpdate(nodes, tileId, { x: NaN, y: 0 }, canMutate), null);

const hydrated = buildGraph({ ...args, nodePositions: filterNodePositions(nodes, saved) });
assert.deepEqual(hydrated.rfNodes.find((node) => node.id === owner.id)?.position,
  original.rfNodes.find((node) => node.id === owner.id)?.position);
assert.deepEqual(hydrated.rfEdges.filter((edge) => edge.type === "produces"),
  original.rfEdges.filter((edge) => edge.type === "produces"));
for (const [nodeId, expected] of Object.entries(saved)) {
  const tile = hydrated.rfNodes.find((node) => node.id === nodeId)!;
  assert.equal(tile.position.x, expected.x);
  assert.equal(tile.position.y, expected.y);
  assert.equal(tile.parentNode, "planspace:history");
}
const lane = hydrated.rfNodes.find((node) => node.id === "planspace:history")!;
assert.ok(lane.width! >= overflowPosition.x + 160);
assert.ok(lane.height! >= overflowPosition.y + 70);
const movedLane = buildGraph({ ...args, nodePositions: saved, lanePositions: { "planspace:history": { x: -500, y: -600 } } });
assert.deepEqual(movedLane.rfNodes.find((node) => node.id === tileId)?.position,
  hydrated.rfNodes.find((node) => node.id === tileId)?.position);
const movedOwner = buildGraph({ ...args, nodePositions: { ...saved, [owner.id]: { x: 300, y: 400 } } });
assert.deepEqual(movedOwner.rfNodes.find((node) => node.id === tileId)?.position,
  hydrated.rfNodes.find((node) => node.id === tileId)?.position);

assert.deepEqual(filterNodePositions([{ ...owner, planspace_id: "new" }], saved), {});
assert.deepEqual(filterNodePositions([{ ...owner, artifacts: [] }], saved), {});
assert.deepEqual(filterNodePositions([{ ...owner, kind: "op" }], saved), {});
assert.deepEqual(filterNodePositions([{ ...owner, artifacts: artifacts.map((artifact) => ({ ...artifact, status: "dropped" })) }], saved), {});
assert.deepEqual(filterNodePositions(nodes, { [tileId]: { ...position, x: Infinity } }), {});
assert.equal(nodePositionUpdate([{ ...owner, artifacts: artifacts.slice(0, 4) }], overflowId, position, canMutate), null);
const hiddenId = artifactNodeId(owner.id, artifacts[4].name);
const hiddenSaved = { [hiddenId]: position };
assert.deepEqual(filterNodePositions(nodes, hiddenSaved), hiddenSaved);
const revealed = buildGraph({ ...args, nodes: [{ ...owner, artifacts: artifacts.slice(2) }], nodePositions: hiddenSaved });
assert.equal(revealed.rfNodes.find((node) => node.id === hiddenId)?.position.x, position.x);
const floatingOwner = { ...owner, settings_snapshot: {} };
const canvasPosition = { ...position, space: "canvas" };
assert.deepEqual(nodePositionUpdate([floatingOwner], tileId, position, canMutate), canvasPosition);
const floating = buildGraph({ ...args, nodes: [floatingOwner], nodePositions: { [tileId]: canvasPosition } });
assert.equal(floating.rfNodes.find((node) => node.id === tileId)?.parentNode, undefined);
assert.equal(floating.rfNodes.find((node) => node.id === tileId)?.position.x, canvasPosition.x);
console.log("产物拖动、位置恢复、归属权限与方向布局测试通过");
