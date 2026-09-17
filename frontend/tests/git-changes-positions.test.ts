import assert from "node:assert/strict";
import type { CommitDescriptor, NodePosition } from "../src/types";
import { buildGraph, LANE, resolveSyncedNodePosition, type BuildGraphArgs } from "../src/canvas/layout";
import {
  captureGitChangesPosition,
  preserveGitRuntimePosition,
  readGitChangesPosition,
  resolveCommitPositionTarget,
  saveGitChangesPosition,
  setGitChangesPositionResolver,
  transferGitPosition,
  type CommitPositionTarget,
} from "../src/canvas/gitPositions";

const baseSha = "1".repeat(40);
const nextSha = "2".repeat(40);
const laterSha = "3".repeat(40);
const ghostId = "commit:ghost";
const headPosition: NodePosition = { x: 615, y: 430, space: "canvas" };
const draggedPosition: NodePosition = { x: -310, y: 870, space: "canvas" };
const commit = (sha: string, parents: string[] = []): CommitDescriptor => ({
  sha, parent_shas: parents, message: "提交", aliases: [], column: 0, external_count_before: 0, live: true,
});
const args: BuildGraphArgs = {
  nodes: [], activeNodeIds: [], nodePositions: {}, contextBundlesByNodeId: {},
  knownPlanspaceIds: [], hiddenPlanspaceIds: [], focusedPlanspaceId: null,
  autoPlanspaceIds: [], canCreateVirtual: false, canMutateGitLayout: true,
  gitCommits: [commit(baseSha)], gitHead: baseSha, gitDirtyCount: 1,
  gitPositions: { [`commit:${baseSha}`]: headPosition },
};
const xy = (position: { x: number; y: number }) => ({ x: position.x, y: position.y });
const positionOf = (graph: ReturnType<typeof buildGraph>, nodeId: string) => {
  const node = graph.rfNodes.find((candidate) => candidate.id === nodeId);
  assert.ok(node, `缺少节点：${nodeId}`);
  return xy(node.position);
};

const stored = new Map<string, string>();
const storage = {
  getItem: (key: string) => stored.get(key) ?? null,
  setItem: (key: string, value: string) => { stored.set(key, value); },
  removeItem: (key: string) => { stored.delete(key); },
};
assert.equal(readGitChangesPosition("project", storage), null);
saveGitChangesPosition("project", draggedPosition, storage);
assert.deepEqual(readGitChangesPosition("project", storage), draggedPosition);
saveGitChangesPosition("project", null, storage);
assert.equal(readGitChangesPosition("project", storage), null);
stored.set("miniclaw2.git-changes-position.v1:project", "invalid");
assert.equal(readGitChangesPosition("project", storage), null);
assert.doesNotThrow(() => saveGitChangesPosition("project", draggedPosition, null));

const original = buildGraph(args);
const originalGhost = positionOf(original, ghostId);
assert.deepEqual(originalGhost, { x: headPosition.x, y: headPosition.y + LANE.trunkStep });
assert.equal(captureGitChangesPosition(), null);
let liveGhost: { x: number; y: number } | null = { ...draggedPosition };
setGitChangesPositionResolver(() => liveGhost);
const captured = captureGitChangesPosition();
assert.deepEqual(captured, draggedPosition);
liveGhost.x += 150;
assert.deepEqual(captured, draggedPosition, "提交点击时应复制坐标，不能随之后的画布变化漂移");
liveGhost = null;
assert.equal(captureGitChangesPosition(), null);
setGitChangesPositionResolver(null);

const nextCommits = [commit(baseSha), commit(nextSha, [baseSha])];
for (const previousPosition of [originalGhost, draggedPosition]) {
  for (const dirtyCount of [0, 1]) {
    const target: CommitPositionTarget = {
      sha: nextSha, position: { ...previousPosition, space: "canvas" },
    };
    const saved = { [`commit:${baseSha}`]: headPosition, [ghostId]: target.position };
    assert.equal(resolveCommitPositionTarget(args.gitCommits!, target, true), null);
    assert.equal(resolveCommitPositionTarget(nextCommits, target, false), null);
    assert.equal(resolveCommitPositionTarget(nextCommits, null, true), null);
    const transfer = resolveCommitPositionTarget(nextCommits, target, true);
    assert.deepEqual(transfer, target);
    const positions = transferGitPosition(saved, transfer);
    assert.equal(positions[ghostId], undefined, "原 changes 锚点应被提交消费");
    assert.deepEqual(saved[ghostId], target.position, "转移不能修改传入快照");
    assert.deepEqual(positions[`commit:${baseSha}`], headPosition);
    const committedArgs = {
      ...args, gitCommits: nextCommits, gitHead: nextSha,
      gitPositions: positions, gitDirtyCount: dirtyCount,
    };
    const committed = buildGraph(committedArgs);
    assert.deepEqual(positionOf(committed, `commit:${nextSha}`), xy(previousPosition));
    if (dirtyCount) {
      assert.deepEqual(positionOf(committed, ghostId), {
        x: previousPosition.x, y: previousPosition.y + LANE.trunkStep,
      });
    } else {
      assert.equal(committed.rfNodes.some((node) => node.id === ghostId), false);
    }
    const newChanges = buildGraph({ ...committedArgs, gitDirtyCount: 2 });
    assert.deepEqual(positionOf(newChanges, ghostId), {
      x: previousPosition.x, y: previousPosition.y + LANE.trunkStep,
    }, "新 changes 不应复用已被提交消费的手动锚点");
    const earlyGraph = buildGraph({ ...committedArgs, gitPositions: saved });
    const runtimeCommit = positionOf(earlyGraph, `commit:${nextSha}`);
    assert.deepEqual(xy(resolveSyncedNodePosition(
      positionOf(committed, `commit:${nextSha}`), runtimeCommit,
      preserveGitRuntimePosition(`commit:${nextSha}`, positions, transfer),
    )), xy(previousPosition), "WebSocket 先渲染提交时，也必须恢复点击时的位置");
    assert.deepEqual(xy(resolveSyncedNodePosition(
      positionOf(newChanges, ghostId), previousPosition,
      preserveGitRuntimePosition(ghostId, positions, transfer),
    )), positionOf(newChanges, ghostId), "仍有未提交改动时，旧 runtime 位置不能覆盖新 changes 布局");
  }
}

const movedHead: NodePosition = { x: 1250, y: 1610, space: "canvas" };
const movedPositions = { [`commit:${baseSha}`]: movedHead };
const moved = buildGraph({ ...args, gitPositions: movedPositions });
assert.deepEqual(xy(resolveSyncedNodePosition(
  positionOf(moved, ghostId), originalGhost,
  preserveGitRuntimePosition(ghostId, movedPositions, null),
)), { x: movedHead.x, y: movedHead.y + LANE.trunkStep }, "未拖动的 changes 应跟随 HEAD 移动");

const advanced = buildGraph({
  ...args, gitCommits: [...nextCommits, commit(laterSha, [nextSha])],
  gitHead: laterSha, gitPositions: movedPositions,
});
assert.deepEqual(xy(resolveSyncedNodePosition(
  positionOf(advanced, ghostId), originalGhost,
  preserveGitRuntimePosition(ghostId, movedPositions, null),
)), { x: movedHead.x, y: movedHead.y + LANE.trunkStep * 3 }, "外部或 agent 提交后，未拖动的 changes 也应跟随新 HEAD");

const pinned = { ...movedPositions, [ghostId]: draggedPosition };
assert.equal(preserveGitRuntimePosition(ghostId, pinned, null), true);
assert.deepEqual(positionOf(buildGraph({ ...args, gitPositions: pinned }), ghostId), xy(draggedPosition));
assert.equal(buildGraph({ ...args, gitPositions: pinned, gitDirtyCount: 0 }).rfNodes.some((node) => node.id === ghostId), false);
assert.deepEqual(positionOf(buildGraph({ ...args, gitPositions: pinned, gitHead: nextSha, gitCommits: nextCommits }), ghostId), xy(draggedPosition), "手动位置应跨 HEAD 变化和暂时隐藏保留");
assert.deepEqual(transferGitPosition(pinned, null), pinned, "没有成功提交信号时不得消费手动锚点");
assert.equal(preserveGitRuntimePosition(`commit:${baseSha}`, {}, null), true);

const first = buildGraph({ ...args, gitCommits: [], gitHead: null, gitPositions: {} });
const firstTarget: CommitPositionTarget = {
  sha: nextSha, position: { ...positionOf(first, ghostId), space: "canvas" },
};
const firstPositions = transferGitPosition({}, resolveCommitPositionTarget([commit(nextSha)], firstTarget, true));
assert.deepEqual(positionOf(buildGraph({
  ...args, gitCommits: [commit(nextSha)], gitHead: nextSha, gitPositions: firstPositions,
}), `commit:${nextSha}`), xy(firstTarget.position), "仓库的首个提交也应原地形成");
assert.equal(resolveCommitPositionTarget(nextCommits, {
  sha: nextSha, position: { x: NaN, y: 0, space: "canvas" },
}, true), null);
assert.equal(resolveCommitPositionTarget([
  { ...commit(laterSha), aliases: [nextSha] },
], firstTarget, true)?.sha, laterSha);

console.log("Git changes 位置回归测试通过");
