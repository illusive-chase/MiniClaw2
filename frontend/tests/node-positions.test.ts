import assert from "node:assert/strict";
import type { NodeInfo, NodePosition } from "../src/types";
import { buildGraph, resizePlanspaceLanes, type BuildGraphArgs, type PlanspaceLaneData } from "../src/canvas/layout";
import { filterNodePositions, nodePositionUpdate } from "../src/canvas/nodePositions";
import { readCanvasViewport, saveCanvasViewport } from "../src/canvas/viewportStorage";
import { filterGitPositions, gitPositionUpdate } from "../src/canvas/gitPositions";
import { filterLanePositions, lanePositionUpdate } from "../src/canvas/lanePositions";

const native = { id: "native", planspace_id: null, settings_snapshot: { active_planspace_id: "history" }, created_at: 1, kind: "agent", state: "done", artifacts: [] } as unknown as NodeInfo;
const foreign = { ...native, id: "foreign", parent_node_id: "native", settings_snapshot: {} } as NodeInfo;
const nodes = [native, foreign];
const position: NodePosition = { x: 30, y: 40, space: "planspace:history" };
const canMutate = (nodeId: string) => nodeId === "native";

assert.deepEqual(nodePositionUpdate(nodes, "native", position, canMutate), position);
assert.equal(nodePositionUpdate(nodes, "foreign", position, canMutate), null);
assert.equal(nodePositionUpdate(nodes, "commit:ghost", position, () => true), null);
assert.equal(nodePositionUpdate(nodes, "native", { x: NaN, y: 0 }, canMutate), null);
assert.equal(nodePositionUpdate(nodes, "native", position, () => false), null);
assert.deepEqual(filterNodePositions(nodes, { native: position, foreign: position, "commit:ghost": position }), { native: position, foreign: position });
assert.deepEqual(filterNodePositions([{ ...native, planspace_id: "new" }], { native: position }), {});
assert.deepEqual(filterNodePositions(nodes, { native: { ...position, x: Infinity } }), {});
assert.deepEqual(filterNodePositions([{ ...native, settings_snapshot: {}, parent_node_id: "native" }], { native: { ...position, space: "canvas" } }), { native: { ...position, space: "canvas" } });

const args: BuildGraphArgs = {
  nodes,
  activeNodeIds: [],
  nodePositions: { native: position, foreign: position },
  canMutateNode: canMutate,
  contextBundlesByNodeId: {},
  knownPlanspaceIds: ["history"],
  hiddenPlanspaceIds: [],
  focusedPlanspaceId: "history",
  autoPlanspaceIds: [],
  canCreateVirtual: true,
  gitCommits: [{ sha: "head", message: "head", aliases: [], column: 0, external_count_before: 0, live: true }],
  gitHead: "head",
  gitDirtyCount: 1,
  templatePortLaneId: "history",
  templatePorts: [{ name: "input", consumers: ["native"] }],
};
const original = buildGraph(args);
const forged = Object.fromEntries(original.rfNodes.filter((item) => !nodes.some((node) => node.id === item.id)).map((item) => [item.id, { x: 999, y: 999 }]));
const changed = buildGraph({ ...args, nodePositions: { ...args.nodePositions, ...forged } });
assert.deepEqual(changed.rfNodes.map((item) => [item.id, item.position]), original.rfNodes.map((item) => [item.id, item.position]));
assert.equal(original.rfNodes.find((item) => item.id === "native")?.draggable, true);
assert.ok(original.rfNodes.filter((item) => item.id !== "native").every((item) => item.draggable === false));
assert.ok(buildGraph({ ...args, canMutateNode: () => false }).rfNodes.every((item) => item.draggable === false));

const commits = [
  { sha: "base", column: 0, parent_shas: [] },
  { sha: "left", column: 0, parent_shas: ["base"] },
  { sha: "right", column: 1, parent_shas: ["base"] },
  { sha: "merge", column: 0, parent_shas: ["left", "right"] },
].map((commit) => ({ ...commit, message: commit.sha, aliases: [], external_count_before: 0, live: true }));
const topology = buildGraph({ ...args, gitCommits: commits, gitHead: "merge" });
const commitPositions = new Map(topology.rfNodes.filter((item) => item.type === "commit").map((item) => [item.id, item.position]));
assert.notEqual(commitPositions.get("commit:left")!.x, commitPositions.get("commit:right")!.x);
assert.ok(commitPositions.get("commit:merge")!.y > commitPositions.get("commit:left")!.y);
assert.ok(commitPositions.get("commit:merge")!.y > commitPositions.get("commit:right")!.y);
assert.ok(commitPositions.get("commit:ghost")!.y > commitPositions.get("commit:merge")!.y);
assert.equal(new Set([...commitPositions.values()].map((position) => `${position.x}:${position.y}`)).size, commitPositions.size);

const values = new Map<string, string>();
const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
const viewport = { x: -30, y: 20, zoom: 1.2 };
saveCanvasViewport("a", viewport, storage, true);
assert.deepEqual(readCanvasViewport("a", storage), viewport);
assert.equal(readCanvasViewport("b", storage), null);
saveCanvasViewport("a", { x: 0, y: 0, zoom: 2 }, storage, false);
assert.deepEqual(readCanvasViewport("a", storage), viewport);
saveCanvasViewport("a", { ...viewport, zoom: 0 }, storage, true);
assert.deepEqual(readCanvasViewport("a", storage), viewport);
values.set("miniclaw2.canvas-viewport.v1:b", "bad-json");
assert.equal(readCanvasViewport("b", storage), null);
assert.equal(readCanvasViewport("a", null), null);
const blocked = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("quota"); } };
assert.equal(readCanvasViewport("a", blocked), null);
assert.doesNotThrow(() => saveCanvasViewport("a", viewport, blocked, true));
const gitId = `commit:${"a".repeat(40)}`;
const gitPosition: NodePosition = { x: -310, y: 620, space: "canvas" };
const ghostPosition: NodePosition = { x: -200, y: 920, space: "canvas" };
assert.deepEqual(gitPositionUpdate(gitId, gitPosition, true), gitPosition);
assert.equal(gitPositionUpdate(gitId, gitPosition, false), null);
assert.equal(gitPositionUpdate("native", gitPosition, true), null);
assert.equal(gitPositionUpdate(gitId, { x: Infinity, y: 0 }, true), null);
assert.deepEqual(filterGitPositions({ [gitId]: gitPosition, native: position, "commit:ghost": position }), { [gitId]: gitPosition });
const gitArgs: BuildGraphArgs = {
  ...args,
  gitCommits: [{ ...args.gitCommits![0], sha: "a".repeat(40) }],
  gitHead: "a".repeat(40),
  gitPositions: { [gitId]: gitPosition, "commit:ghost": ghostPosition },
  canMutateGitLayout: true,
};
const anchored = buildGraph(gitArgs);
assert.deepEqual(anchored.rfNodes.find((item) => item.id === gitId)?.position, gitPosition);
assert.deepEqual(anchored.rfNodes.find((item) => item.id === "commit:ghost")?.position, ghostPosition);
assert.ok(anchored.rfNodes.filter((item) => item.type === "commit").every((item) => item.draggable));
const readonlyGit = buildGraph({ ...gitArgs, canMutateGitLayout: false });
assert.ok(readonlyGit.rfNodes.filter((item) => item.type === "commit").every((item) => !item.draggable));
assert.deepEqual(readonlyGit.rfNodes.find((item) => item.id === gitId)?.position, gitPosition);
const reappeared = buildGraph({ ...gitArgs, gitDirtyCount: 0 });
assert.equal(reappeared.rfNodes.find((item) => item.id === "commit:ghost"), undefined);
assert.deepEqual(buildGraph(gitArgs).rfNodes.find((item) => item.id === "commit:ghost")?.position, ghostPosition);
const laneId = "planspace:history";
const lanePosition: NodePosition = { x: -1704, y: 3480, space: "canvas" };
assert.deepEqual(lanePositionUpdate(laneId, lanePosition, true), lanePosition);
assert.equal(lanePositionUpdate(laneId, lanePosition, false), null);
assert.equal(lanePositionUpdate("commit:ghost", lanePosition, true), null);
assert.equal(lanePositionUpdate(laneId, { x: NaN, y: 0 }, true), null);
assert.deepEqual(filterLanePositions({ [laneId]: lanePosition, native: position, "planspace:bad": position }), { [laneId]: lanePosition });
const laneArgs: BuildGraphArgs = {
  ...gitArgs,
  knownPlanspaceIds: ["history", "empty"],
  lanePositions: { [laneId]: lanePosition },
  canMutateLaneLayout: true,
};
const pinned = buildGraph(laneArgs);
const lane = pinned.rfNodes.find((item) => item.id === laneId)!;
assert.deepEqual(lane.position, lanePosition);
assert.equal(lane.draggable, true);
assert.equal(lane.dragHandle, ".planspace-lane-drag-handle");
assert.equal((lane.data as PlanspaceLaneData).positionPinned, true);
assert.deepEqual(pinned.rfNodes.find((item) => item.id === "native")?.position, anchored.rfNodes.find((item) => item.id === "native")?.position);
assert.deepEqual(pinned.rfNodes.find((item) => item.id === "foreign")?.position, anchored.rfNodes.find((item) => item.id === "foreign")?.position);
assert.deepEqual(pinned.rfNodes.find((item) => item.id === gitId)?.position, gitPosition);
const grownChildren = pinned.rfNodes.map((item) => item.id === "native" ? { ...item, position: { x: 40, y: 9000 } } : item);
for (const shrink of [false, true]) {
  const resized = resizePlanspaceLanes(grownChildren, new Set([laneId]), shrink);
  assert.deepEqual(resized.find((item) => item.id === laneId)?.position, lanePosition);
  assert.ok((resized.find((item) => item.id === laneId)?.height ?? 0) > 9000);
}
for (const extra of [
  { knownPlanspaceIds: ["empty", "history"] },
  { focusedPlanspaceId: "empty" },
  { canMutateLaneLayout: false },
  { gitCommits: [...gitArgs.gitCommits!, { ...gitArgs.gitCommits![0], sha: "b".repeat(40), column: 4 }] },
]) {
  assert.deepEqual(buildGraph({ ...laneArgs, ...extra }).rfNodes.find((item) => item.id === laneId)?.position, lanePosition);
}
assert.equal(buildGraph({ ...laneArgs, canMutateLaneLayout: false }).rfNodes.find((item) => item.id === laneId)?.draggable, false);
assert.equal(buildGraph({ ...laneArgs, hiddenPlanspaceIds: ["history"] }).rfNodes.find((item) => item.id === laneId), undefined);
assert.deepEqual(buildGraph(laneArgs).rfNodes.find((item) => item.id === laneId)?.position, lanePosition);
const defaultLanePosition = original.rfNodes.find((item) => item.id === laneId)!.position;
const obstacleGraph = buildGraph({ ...laneArgs, knownPlanspaceIds: ["empty", "history"], lanePositions: { [laneId]: defaultLanePosition } });
const obstacle = obstacleGraph.rfNodes.find((item) => item.id === laneId)!;
const autoLane = obstacleGraph.rfNodes.find((item) => item.id === "planspace:empty")!;
assert.deepEqual(obstacle.position, defaultLanePosition);
assert.ok(autoLane.position.y >= obstacle.position.y + obstacle.height!);
const emptyPinned = buildGraph({ ...laneArgs, lanePositions: { "planspace:empty": { x: -500, y: -1000 } } });
assert.deepEqual(emptyPinned.rfNodes.find((item) => item.id === "planspace:empty")?.position, { x: -500, y: -1000 });
console.log("节点位置、Git／方向持久化与浏览器视角测试通过");
