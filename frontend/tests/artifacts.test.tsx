import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server.browser";

import { artifactRawUrl } from "../src/api";
import { artifactExtension, artifactSelection } from "../src/artifactSelection";
import { artifactNodeId, buildGraph } from "../src/canvas/layout";
import { SvgArtifactPreview } from "../src/panel/SvgArtifactPreview";
import type { ArtifactRef, NodeInfo } from "../src/types";

const artifact: ArtifactRef = {
  name: '图表 & "说明".svg',
  status: "published",
  bytes: 100,
  mtime: 1,
  sha256: "svg-hash",
};
const owner: NodeInfo = {
  id: "svg-node",
  project_id: "svg-project",
  kind: "agent",
  state: "done",
  provider: null,
  prompt: "生成 SVG",
  scheduled_deps: [],
  created_at: 1,
  artifacts: [artifact, { ...artifact, name: "dropped.svg", status: "dropped" }],
};

for (const ext of ["md", "json", "html", "svg"]) {
  assert.equal(artifactExtension(`report.${ext}`), ext);
}
for (const name of ["report", "svg", ".svg", "report.SVG", "report.svg.html.exe", "report.svgz"]) {
  assert.equal(artifactExtension(name), null);
}

const graph = buildGraph({
  nodes: [owner],
  activeNodeIds: [],
  layoutHints: {},
  contextBundlesByNodeId: {},
  knownPlanspaceIds: [],
  hiddenPlanspaceIds: [],
  focusedPlanspaceId: null,
  autoPlanspaceIds: [],
  canCreateVirtual: false,
});
const tiles = graph.rfNodes.filter((node) => node.type === "artifact");
assert.equal(tiles.length, 1);
const tile = tiles[0];
assert.equal(tile.id, artifactNodeId(owner.id, artifact.name));
assert.equal(tile.selectable, true);
assert.ok(graph.rfEdges.some((edge) => (
  edge.type === "produces" && edge.source === owner.id && edge.target === tile.id
)));
assert.deepEqual(artifactSelection(tile.data.ownerNodeId, tile.data.artifact), {
  kind: "artifact", nodeId: owner.id, name: artifact.name, ext: "svg",
});
for (const ref of [null, { ...artifact, status: "dropped" as const }, { ...artifact, name: "x.exe" }]) {
  assert.deepEqual(artifactSelection(owner.id, ref), { kind: "agent", nodeId: owner.id });
}

const rawUrl = artifactRawUrl("project/id", "node/id", artifact.name);
assert.equal(rawUrl, `/sessions/project%2Fid/nodes/node%2Fid/artifacts/${encodeURIComponent(artifact.name)}?raw=1`);
const preview = renderToStaticMarkup(<SvgArtifactPreview name={artifact.name} rawUrl={rawUrl} />);
assert.ok(preview.includes(`<img alt="图表 &amp; &quot;说明&quot;.svg" src="${rawUrl}"`));
assert.ok(preview.includes('referrerPolicy="no-referrer"'));
assert.ok(preview.includes("object-contain"));
assert.doesNotMatch(preview, /<(iframe|object|embed|svg|a)\b/);
console.log("SVG 分类、画布选择和图片预览测试通过");
