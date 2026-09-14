import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { getContextBundleSources } from "../src/api";
import { buildGraph, type BuildGraphArgs } from "../src/canvas/layout";
import { ContextBundleSourcesLoader, type ContextBundleSourcesByNodeId } from "../src/contextBundleSources";
import type { ContextBundle, NodeInfo } from "../src/types";

function node(id: string, extra: Partial<NodeInfo> = {}): NodeInfo {
  return {
    id, project_id: "project", kind: "agent", state: "done", provider: null,
    prompt: "测试", created_at: 1, context_bundle_id: `bundle-${id}`, planspace_id: "lane",
    ...extra,
  };
}

const full: ContextBundle = {
  bundle_id: "bundle", created_at: 1, active_planspace: { id: "lane", color: "rose" },
  sources: [{
    scope: "contextspace", kind: "principle", path: "principles/test.md", sha256: "hash",
    chars: 100, injection: "system", plug_id: "principles.test",
  }],
  system_text: "系统正文".repeat(1000), turn_text: "轮次正文".repeat(1000),
};
const sources = { sources: full.sources, active_planspace: full.active_planspace };
const nodes = Array.from({ length: 2000 }, (_, index) => node(`node-${index}`, {
  created_at: index, parent_node_id: index > 0 ? `node-${index - 1}` : null,
  scheduled_deps: index > 0 ? [`node-${index - 1}`] : [],
}));
const args: BuildGraphArgs = {
  nodes, activeNodeIds: [], nodePositions: {}, knownPlanspaceIds: ["lane"],
  hiddenPlanspaceIds: [], autoPlanspaceIds: [], focusedPlanspaceId: "lane", canCreateVirtual: false,
  contextBundlesByNodeId: Object.fromEntries(nodes.map((item) => [item.id, full])),
};
const graph = buildGraph(args);
const projected = Object.fromEntries(nodes.map((item) => [item.id, sources]));
assert.deepEqual(graph, buildGraph({ ...args, contextBundlesByNodeId: projected }));
assert.ok(graph.rfNodes.some((item) => item.data.color?.name === "rose"));
assert.ok(graph.rfEdges.some((edge) => edge.type === "loads"));

const requests: Array<{
  sessionId: string; nodeIds?: string[]; signal?: AbortSignal;
  resolve: (result: ContextBundleSourcesByNodeId) => void; reject: (error: Error) => void;
}> = [];
const publications: ContextBundleSourcesByNodeId[] = [];
const errors: unknown[] = [];
const loader = new ContextBundleSourcesLoader("project", (sessionId, nodeIds, signal) =>
  new Promise((resolve, reject) => requests.push({ sessionId, nodeIds, signal, resolve, reject })),
  (bundles) => publications.push(bundles), (error) => errors.push(error));
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
loader.update(nodes);
loader.update(nodes.map((item) => ({ ...item, rev: 2 })));
assert.equal(requests.length, 1);
assert.equal(requests[0].nodeIds, undefined);
requests[0].resolve(projected);
await settle();
assert.equal(publications.length, 1);
assert.equal(Object.keys(publications[0]).length, 2000);
loader.update(nodes);
assert.equal(requests.length, 1);

const added = node("added");
const missing = node("missing", { context_bundle_id: null, context_bundle_path: "legacy.json" });
loader.update([...nodes, added, missing]);
assert.deepEqual(requests[1].nodeIds, ["added", "missing"]);
const newer = node("added", { context_bundle_id: "new-bundle" });
loader.update([...nodes, newer, missing, node("late")]);
assert.equal(requests.length, 2);
requests[1].resolve({ added: sources, missing: null });
await settle();
assert.equal(publications.at(-1)?.added, undefined);
assert.equal(publications.at(-1)?.missing, null);
assert.deepEqual(requests[2].nodeIds, ["added", "late"]);
requests[2].resolve({ added: sources, late: sources });
await settle();
assert.equal(publications.at(-1)?.added, sources);
loader.update([newer]);
assert.deepEqual(Object.keys(publications.at(-1)!), ["added"]);
assert.equal(requests.length, 3);

loader.update([newer, node("failure")]);
requests[3].reject(new Error("模拟失败"));
await settle();
assert.equal(requests.length, 4);
assert.equal(errors.length, 1);
loader.update([newer, node("failure")]);
assert.deepEqual(requests[4].nodeIds, ["failure"]);
requests[4].resolve({});
await settle();
assert.equal(publications.at(-1)?.failure, null);
loader.update([newer, node("failure")]);
assert.equal(requests.length, 5);

loader.update([newer, node("pending")]);
const publicationCount = publications.length;
loader.dispose();
assert.equal(requests[5].signal?.aborted, true);
requests[5].resolve({ pending: sources });
await settle();
assert.equal(publications.length, publicationCount);
loader.update(nodes);
assert.equal(requests.length, 6);

const filteredRequests: Array<string[] | undefined> = [];
const filterLoader = new ContextBundleSourcesLoader("project", async (_sessionId, nodeIds) => {
  filteredRequests.push(nodeIds);
  return {};
}, () => {}, () => {});
filterLoader.update([
  node("op", { kind: "op" }), node("running", { state: "running" }),
  node("virtual", { state: "virtual" }), node("absent", { context_bundle_id: null }),
]);
assert.equal(filteredRequests.length, 0);
filterLoader.update([node("cancelled", { state: "cancelled" })]);
await settle();
filterLoader.update(nodes);
await settle();
assert.deepEqual(filteredRequests, [undefined, undefined]);
filterLoader.dispose();

let fetchedUrl = "";
const originalFetch = globalThis.fetch;
try {
  globalThis.fetch = async (input) => {
    fetchedUrl = String(input);
    return new Response(JSON.stringify({ added: sources }));
  };
  assert.deepEqual(await getContextBundleSources("project"), { added: sources });
  assert.equal(fetchedUrl, "/sessions/project/context-bundles");
  await getContextBundleSources("project", ["node with space", "another"]);
  assert.equal(fetchedUrl, "/sessions/project/context-bundles?node_ids=node+with+space&node_ids=another");
} finally {
  globalThis.fetch = originalFetch;
}

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
assert.ok(app.includes("useContextBundleSources("));
assert.ok(!app.includes("setContextBundlesByNodeId"));
assert.ok(!app.includes("inflightBundleFetchRef"));
console.log("上下文批量读取回归通过：2000 节点画布一致、单次发布、增量合并、过期响应隔离和失败重试");
