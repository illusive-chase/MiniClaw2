import assert from "node:assert/strict";
import { buildGraph, type BuildGraphArgs } from "../src/canvas/layout";
import { NodeDetailCache } from "../src/nodeDetailCache";
import { toNodeInfo } from "../src/nodeProjection";
import type { NodeDetail } from "../src/types";

function detail(id: string, rev = 1, projectId = "project"): NodeDetail {
  return {
    id, rev, project_id: projectId, kind: "agent", state: "done", provider: null,
    prompt: "测试🙂".repeat(60) + "{{late_argument}} {{input.source}}",
    system_context_snapshot: "系统上下文".repeat(1000),
    launch_instructions_snapshot: "启动规则".repeat(1000),
    settings_snapshot: {
      active_planspace_id: "lane",
      skill_audit: [{ id: "skills.search", name: "搜索", used: true }],
    },
    planspace_id: "lane", created_at: rev,
  };
}

const full = detail("node");
const slim = toNodeInfo(full);
assert.equal(Array.from(slim.prompt).length, 120);
assert.equal(slim.prompt_truncated, true);
assert.deepEqual(slim.prompt_argument_names, ["late_argument"]);
assert.equal(slim.settings_snapshot, full.settings_snapshot);
assert.ok(!("system_context_snapshot" in slim));
assert.ok(!("launch_instructions_snapshot" in slim));
assert.equal(toNodeInfo(slim), slim);
assert.equal(toNodeInfo({ ...full, prompt: "🙂".repeat(120) }).prompt_truncated, false);
assert.equal(toNodeInfo({ ...full, prompt: "" }).prompt_truncated, false);
assert.deepEqual(toNodeInfo({ ...full, prompt_draft: "{{draft}}" }).prompt_argument_names, ["draft"]);

const nodes: NodeDetail[] = Array.from({ length: 2000 }, (_, index) => ({
  ...detail(`node-${index}`),
  created_at: index,
  scheduled_deps: index > 0 ? [`node-${index - 1}`] : [],
  parent_node_id: index > 0 ? `node-${index - 1}` : null,
  artifacts: index % 10 === 0 ? [{
    name: "result.md", bytes: 100, mtime: 1, sha256: "artifact", status: "published",
  }] : [],
}));
const args: BuildGraphArgs = {
  nodes, activeNodeIds: [], nodePositions: {}, contextBundlesByNodeId: {},
  knownPlanspaceIds: ["lane"], hiddenPlanspaceIds: [], autoPlanspaceIds: [],
  focusedPlanspaceId: "lane",
  canCreateVirtual: false,
  skills: [{ id: "skills.search", slug: "search", title: "搜索", description: "", path: "/skills/search" }],
};
const graph = buildGraph(args);
assert.ok(graph.rfNodes.some((node) => node.type === "artifact"));
assert.ok(graph.rfNodes.some((node) => node.type === "context" && node.data.kind === "skill"));
assert.deepEqual(graph, buildGraph({ ...args, nodes: nodes.map(toNodeInfo) }));

const requests: Array<{
  sessionId: string; nodeId: string;
  resolve: (value: NodeDetail) => void; reject: (error: Error) => void;
}> = [];
const cache = new NodeDetailCache((sessionId, nodeId) => new Promise((resolve, reject) => {
  requests.push({ sessionId, nodeId, resolve, reject });
}), 2);
const first = cache.load("project", "first", 1);
assert.equal(cache.load("project", "first", 1), first);
requests[0].resolve(detail("first"));
await first;
assert.equal((await cache.load("project", "first", 1)).id, "first");
assert.equal(requests.length, 1);
const old = cache.load("project", "first", 2);
const latest = cache.load("project", "first", 3);
requests[2].resolve(detail("first", 3));
await latest;
requests[1].resolve(detail("first", 2));
await old;
assert.equal(cache.get("project", "first", 3)?.rev, 3);
assert.equal(cache.get("project", "first", 1), null);
const foreign = cache.load("other-project", "first", 3);
requests[3].resolve(detail("first", 3, "other-project"));
await foreign;
assert.equal(cache.get("other-project", "first", 3)?.project_id, "other-project");
const failing = cache.load("project", "missing", 1);
requests[4].reject(new Error("读取失败"));
await assert.rejects(failing, /读取失败/);
const retry = cache.load("project", "missing", 1);
requests[5].resolve(detail("missing"));
await retry;
const stale = cache.load("project", "stale", 2);
requests[6].resolve(detail("stale", 1));
await assert.rejects(stale, /版本已过期/);
assert.equal(cache.get("project", "stale", 1), null);
console.log("节点投影、2000 节点画布一致性及详情缓存回归通过");
