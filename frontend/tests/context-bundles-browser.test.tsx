import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { ContextNodePanel } from "../src/panel/ContextNodePanel";
import { useContextBundleSources } from "../src/useContextBundleSources";
import { useNodeContextBundle } from "../src/useNodeContextBundle";
import type { ContextBundle, ContextBundleSources, NodeInfo } from "../src/types";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const container = document.createElement("div");
document.body.append(container);
const root = createRoot(container);
const requests: Array<{
  url: string; signal?: AbortSignal | null; resolve: (response: Response) => void;
}> = [];
globalThis.fetch = (input, init) => new Promise((resolve) => {
  requests.push({ url: String(input), signal: init?.signal, resolve });
});
const publications: Array<Record<string, ContextBundleSources | null>> = [];
const sources: ContextBundleSources = {
  sources: [{
    scope: "project", kind: "project", path: "/project/CONTEXT.md", sha256: "hash",
    chars: 100, injection: "system",
  }], active_planspace: { id: "lane", color: "rose" },
};
const nodes: NodeInfo[] = Array.from({ length: 2000 }, (_, index) => ({
  id: `node-${index}`, project_id: "project", kind: "agent", state: "done", provider: null,
  prompt: "测试", created_at: index, context_bundle_id: `bundle-${index}`,
}));
function full(text: string): ContextBundle {
  return {
    ...sources, bundle_id: "full", created_at: 1,
    system_text: `/project/CONTEXT.md\n\`\`\`\n${text}\n\`\`\``, turn_text: "轮次正文",
  };
}

type ProbeProps = {
  projectId: string | null; nodes: NodeInfo[]; selectedId?: string; contextId?: string;
};

function Probe({ projectId, nodes, selectedId, contextId }: ProbeProps) {
  const bundles = useContextBundleSources(projectId, nodes);
  const detail = useNodeContextBundle(projectId ?? undefined, nodes.find((node) => node.id === selectedId));
  useEffect(() => {
    if (Object.keys(bundles).length > 0) publications.push(bundles);
  }, [bundles]);
  return <div data-count={Object.keys(bundles).length} data-loading={String(detail.loading)}>
    <output>{detail.bundle?.system_text ?? ""}</output>
    {contextId && <ContextNodePanel
      sessionId={projectId ?? undefined} sampleNodeId={contextId}
      identityKey="project::project::/project/CONTEXT.md" path="/project/CONTEXT.md"
      loadedByNodeIds={Object.keys(bundles)} nodesById={new Map(nodes.map((node) => [node.id, node]))}
      sampleBundle={bundles[contextId] ?? null} onSelectConsumer={() => {}}
    />}
  </div>;
}

function check(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
async function render(props: ProbeProps) {
  await act(async () => { root.render(<Probe {...props} />); });
}
async function respond(index: number, value: unknown, status = 200) {
  await act(async () => {
    requests[index].resolve(new Response(JSON.stringify(value), {
      status, headers: { "Content-Type": "application/json" },
    }));
  });
}
function count() { return container.firstElementChild?.getAttribute("data-count"); }

async function run() {
  await render({ projectId: null, nodes });
  check(requests.length === 0, "节点列表就绪前不应预取");
  await render({ projectId: "project", nodes });
  check(requests.length === 1 && requests[0].url === "/sessions/project/context-bundles", "打开 2000 节点项目应只请求一次摘要");
  await render({ projectId: "project", nodes: nodes.map((node) => ({ ...node, rev: 2 })) });
  check(requests.length === 1, "列表重渲染不应重复在途请求");
  await respond(0, Object.fromEntries(nodes.map((node) => [node.id, sources])));
  check(count() === "2000" && publications.length === 1, "整批摘要应一次发布");

  const running: NodeInfo = { ...nodes[0], id: "live", state: "running", context_bundle_id: "live-bundle" };
  await render({ projectId: "project", nodes: [...nodes, running] });
  check(requests.length === 1, "运行节点不应启动终态预取");
  const updated = [...nodes, { ...running, state: "done" as const }];
  await render({ projectId: "project", nodes: updated });
  check(requests[1].url === "/sessions/project/context-bundles?node_ids=live", "新终态节点应增量补取");
  await respond(1, { live: sources });
  check(count() === "2001" && publications.length === 2, "增量不应丢失已加载摘要");

  await render({ projectId: "project", nodes: updated, selectedId: "node-0" });
  check(requests[2].url.endsWith("/nodes/node-0/context-bundle"), "选中节点才读取正文");
  await respond(2, full("节点零正文"));
  check(container.querySelector("output")?.textContent?.includes("节点零正文"), "节点正文应可见");
  check(publications.length === 2, "正文不可回灌共享摘要");
  check(publications.every((snapshot) => Object.values(snapshot).every((bundle) =>
    !bundle || !("system_text" in bundle) && !("turn_text" in bundle))), "共享状态不能包含正文");

  await render({ projectId: "project", nodes: updated, contextId: "node-0" });
  check(requests[3].url.endsWith("/nodes/node-0/context-bundle"), "上下文卡片应按需读取样本正文");
  check(container.textContent?.includes("正在加载上下文正文"), "卡片应显示加载状态");
  await render({ projectId: "project", nodes: updated, contextId: "node-1" });
  check(requests[3].signal?.aborted, "切换样本应取消旧请求");
  await respond(4, full("节点一正文"));
  await respond(3, full("过期正文"));
  check(container.textContent?.includes("节点一正文") && !container.textContent?.includes("过期正文"), "旧样本不能覆盖当前正文");

  await render({ projectId: "project", nodes: updated, contextId: "node-2" });
  await respond(5, {}, 503);
  const retry = Array.from(container.querySelectorAll("button"))
    .find((button) => button.textContent?.includes("点击重试"));
  check(retry, "正文读取失败应支持重试");
  await act(async () => { retry.click(); });
  await respond(6, full("重试成功正文"));
  check(container.textContent?.includes("重试成功正文"), "重试应恢复上下文正文");

  const changed = [{ ...nodes[0], context_bundle_id: "replacement" }, ...nodes.slice(1)];
  await render({ projectId: "project", nodes: changed });
  check(requests[7].url.endsWith("context-bundles?node_ids=node-0"), "引用改变应失效");
  await render({ projectId: "other", nodes: [] });
  check(requests[7].signal?.aborted && count() === "0", "切换项目应清空并取消在途批量请求");
  await render({ projectId: "project", nodes: changed });
  check(requests[8].url === "/sessions/project/context-bundles", "返回项目应重新整批读取");
  await respond(8, { "node-0": sources });
  const currentPublications = publications.length;
  await respond(7, { "node-0": { sources: [] } });
  check(publications.length === currentPublications, "前次同项目响应不能污染重新打开后的摘要");
  await act(async () => { root.unmount(); });
}

run().then(() => {
  document.body.dataset.testResult = "passed";
}, (error) => {
  document.body.dataset.testResult = "failed";
  document.body.append(String(error?.stack ?? error));
});
