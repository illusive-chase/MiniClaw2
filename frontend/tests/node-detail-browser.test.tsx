import { act } from "react";
import { createRoot } from "react-dom/client";
import { toNodeInfo } from "../src/nodeProjection";
import { useNodeDetail } from "../src/useNodeDetail";
import type { NodeDetail, NodeInfo } from "../src/types";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const container = document.createElement("div");
document.body.append(container);
const root = createRoot(container);
const requests: Array<{ url: string; resolve: (response: Response) => void }> = [];
globalThis.fetch = (input) => new Promise((resolve) => {
  requests.push({ url: String(input), resolve });
});

function detail(id: string, rev = 1, projectId = "project"): NodeDetail {
  return {
    id, rev, project_id: projectId, kind: "agent", state: "done", provider: null,
    prompt: `${projectId}/${id}/${rev}:` + "完整提示词".repeat(50), created_at: 1,
    system_context_snapshot: "系统快照", launch_instructions_snapshot: "启动快照",
  };
}

function Probe({ projectId, node }: { projectId: string; node?: NodeInfo }) {
  const state = useNodeDetail(projectId, node);
  return <div data-node-id={state.detail?.id ?? ""} data-rev={state.detail?.rev ?? ""}
    data-loading={String(state.loading)} data-error={state.error ?? ""}>
    <p>{state.detail?.prompt ?? node?.prompt ?? ""}</p>
    <button onClick={state.retry}>重试</button>
  </div>;
}

function check(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

async function select(id?: string, rev = 1, projectId = "project") {
  await act(async () => {
    root.render(<Probe projectId={projectId} node={id ? toNodeInfo(detail(id, rev, projectId)) : undefined} />);
  });
}

async function respond(index: number, value: NodeDetail | string, status = 200) {
  await act(async () => {
    requests[index].resolve(new Response(JSON.stringify(value), {
      status, headers: { "Content-Type": "application/json" },
    }));
  });
}

function view() { return container.firstElementChild as HTMLElement; }

try {
  await select("first");
  check(requests.length === 1, "选中时只请求一个节点详情");
  check(view().dataset.loading === "true", "详情到达前保留预览与加载状态");
  check(container.textContent?.includes(toNodeInfo(detail("first")).prompt), "加载时展示列表预览");
  await select("second");
  await respond(1, detail("second"));
  await respond(0, detail("first"));
  check(view().dataset.nodeId === "second", "旧选择的迟到响应不能覆盖新选择");
  check(container.textContent?.includes(detail("second").prompt), "选中后显示完整提示词");
  await select("first");
  check(Number(requests.length) === 2, "同版本重选命中缓存");
  check(view().dataset.nodeId === "first", "缓存立即恢复所选详情");

  await select("first", 2);
  check(view().dataset.loading === "true", "版本变更触发重新读取");
  await select("first", 3);
  await respond(3, detail("first", 3));
  await respond(2, detail("first", 2));
  check(view().dataset.rev === "3", "迟到的旧版本不能覆盖新版本");
  await select("first", 3, "other-project");
  check(view().dataset.nodeId === "", "跨项目不显示另一项目的缓存");
  await respond(4, detail("first", 3, "other-project"));
  check(container.textContent?.includes("other-project/first/3"), "缓存按项目隔离");

  await select("failure");
  await respond(5, "读取失败", 500);
  check(Boolean(view().dataset.error), "失败时显示错误并提供重试");
  await act(async () => { container.querySelector("button")?.click(); });
  await respond(6, detail("failure"));
  check(view().dataset.nodeId === "failure" && !view().dataset.error, "重试成功后恢复详情");

  await select("deleted");
  await select();
  await respond(7, detail("deleted"));
  check(view().dataset.nodeId === "" && view().dataset.loading === "false", "取消选择或删除后迟到响应不能恢复旧详情");
  await act(async () => { root.unmount(); });
  document.body.dataset.testResult = "passed";
} catch (error) {
  document.body.dataset.testResult = "failed";
  const result = document.createElement("pre");
  result.textContent = error instanceof Error ? error.stack ?? error.message : String(error);
  document.body.append(result);
}
