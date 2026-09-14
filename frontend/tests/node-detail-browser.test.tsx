import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { draftStashKey, writeStashedDraft } from "../src/draftStash";
import { toNodeInfo } from "../src/nodeProjection";
import { AgentPanel, virtualDraftFromNode } from "../src/panel/AgentPanel";
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

function button(text: string) {
  const found = Array.from(container.querySelectorAll("button"))
    .find((candidate) => candidate.textContent === text);
  check(found, `应显示按钮：${text}`);
  return found;
}

function promptInput() {
  const found = container.querySelector<HTMLTextAreaElement>('textarea[rows="8"]');
  check(found, "应显示提示词编辑器");
  return found;
}

async function editPrompt(value: string) {
  const input = promptInput();
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function mountDraftPanel(id: string, stashedPrompt?: string, kind: NodeDetail["kind"] = "agent") {
  await act(async () => { root.render(null); });
  let serverNode: NodeDetail = {
    ...detail(id, 1, "draft-project"), kind, state: "virtual", category: "regular",
    model_preset_id: "gpt-5.5", prompt_draft: "已保存的提示词",
  };
  const saves: string[] = [];
  const promotions: string[] = [];
  let saveBarrier: Promise<void> | undefined;
  if (stashedPrompt !== undefined) {
    const baseline = virtualDraftFromNode(serverNode);
    check(writeStashedDraft(draftStashKey(serverNode.project_id, id), {
      savedAt: Date.now(), baseline, draft: { ...baseline, promptDraft: stashedPrompt },
    }), "应写入本地草稿留存");
  }
  function DraftPanelProbe() {
    const [node, setNode] = useState(() => toNodeInfo(serverNode));
    const state = useNodeDetail(serverNode.project_id, node);
    return <AgentPanel
      sessionId={serverNode.project_id} node={node} detail={state.detail}
      detailLoading={state.loading} detailError={state.error} onRetryDetail={state.retry}
      nodesById={new Map([[node.id, node]])} modelPresets={[]}
      events={[]} eventsLoading={false} diff={null} diffLoading={false}
      contextBundle={null} contextBundleLoading={false} pendingGate={null} pendingReview={null}
      onResolveReview={() => {}} onCreateContinuationVirtual={() => {}}
      onPromoteVirtual={async () => { promotions.push(serverNode.prompt_draft ?? ""); }}
      onDequeueNode={async () => {}}
      onUpdateVirtual={async (_nodeId, payload) => {
        saves.push(payload.prompt_draft ?? "");
        await saveBarrier;
        serverNode = {
          ...serverNode, rev: (serverNode.rev ?? 0) + 1,
          prompt_draft: payload.prompt_draft ?? serverNode.prompt_draft,
        };
        setNode(toNodeInfo(serverNode));
        return serverNode;
      }}
      onInterruptNode={() => {}} onRerunNode={() => {}}
      canInterrupt={false} canRerun={false} canMutate={true} mutationLock={null}
      isManualPlanspace={() => true} focusRequestVersion={0} activityFocusRequestVersion={0}
      onSelectArtifact={() => {}}
    />;
  }
  const requestIndex = requests.length;
  await act(async () => { root.render(<DraftPanelProbe />); });
  return {
    requestIndex, saves, promotions, serverNode: () => serverNode,
    blockSave: (barrier: Promise<void>) => { saveBarrier = barrier; },
  };
}

async function testDraftPromotion() {
  const panel = await mountDraftPanel("restored-draft", "浏览器中未保存的提示词");
  check(button("Promote").disabled, "详情加载前禁止提升节点");
  await act(async () => { button("Promote").click(); });
  check(panel.promotions.length === 0 && panel.saves.length === 0, "加载时不得提交或运行旧提示词");
  await respond(panel.requestIndex, "读取失败", 500);
  check(button("Promote").disabled, "首次加载失败后仍禁止提升节点");
  await act(async () => { button("Promote").click(); });
  check(panel.promotions.length === 0, "详情错误时不能绕过草稿恢复");
  await act(async () => { button("重试").click(); });
  await respond(panel.requestIndex + 1, panel.serverNode());
  check(promptInput().value === "浏览器中未保存的提示词", "详情到达后恢复本地提示词");
  check(!button("Promote").disabled, "草稿恢复后才允许提升节点");
  let releaseSave!: () => void;
  panel.blockSave(new Promise<void>((resolve) => { releaseSave = resolve; }));
  await act(async () => { button("Promote").click(); });
  check(panel.saves[0] === "浏览器中未保存的提示词", "提升前先提交恢复的草稿");
  check(panel.promotions.length === 0, "草稿保存完成前不能运行节点");
  await act(async () => { releaseSave(); });
  check(panel.promotions[0] === "浏览器中未保存的提示词", "运行的提示词必须包含恢复的修改");

  const invalid = await mountDraftPanel("invalid-draft", "");
  await respond(invalid.requestIndex, invalid.serverNode());
  check(promptInput().value === "" && button("Promote").disabled, "恢复的空草稿不能沿用持久化提示词的就绪状态");
  await editPrompt("已补全的草稿");
  check(!button("Promote").disabled, "补全草稿后可提升节点");

  const verifier = await mountDraftPanel("verifier", undefined, "verifier");
  check(button("Promote").disabled, "验证节点也应等待详情加载");
  await respond(verifier.requestIndex, verifier.serverNode());
  check(!button("Promote").disabled, "没有草稿编辑器的验证节点仍可提升");
  await act(async () => { button("Promote").click(); });
  check(verifier.promotions.length === 1 && verifier.saves.length === 0, "验证节点无需调用草稿保存接口");
}

async function testAutosaveRefresh() {
  const originalSetInterval = window.setInterval;
  const originalClearInterval = window.clearInterval;
  let autosave: (() => void) | undefined;
  window.setInterval = (handler, delay) => {
    check(typeof handler === "function" && delay === 10_000, "草稿应使用十秒自动保存周期");
    autosave = () => handler();
    return 1;
  };
  window.clearInterval = () => {};
  try {
    const panel = await mountDraftPanel("autosave-draft");
    await respond(panel.requestIndex, panel.serverNode());
    const input = promptInput();
    input.focus();
    await editPrompt("自动保存的草稿");
    check(autosave, "手动方向应启用自动保存");
    const refreshIndex = requests.length;
    await act(async () => { autosave?.(); });
    check(panel.saves[0] === "自动保存的草稿", "自动保存应提交当前草稿");
    check(requests.length === refreshIndex + 1, "保存导致版本变化并重新读取详情");
    check(promptInput() === input && !input.matches(":disabled"), "后台读取详情不能禁用或重建编辑器");
    check(document.activeElement === input, "自动保存后仍保留输入焦点");
    await editPrompt("自动保存后继续输入");
    await respond(refreshIndex, panel.serverNode());
    check(input.value === "自动保存后继续输入", "详情响应不能覆盖保存期间继续输入的内容");
    check(document.activeElement === input, "详情更新后仍保留输入焦点");

    const failedRefreshIndex = requests.length;
    await act(async () => { autosave?.(); });
    await respond(failedRefreshIndex, "刷新失败", 500);
    check(!input.matches(":disabled") && document.activeElement === input, "后台刷新失败也不应打断已有草稿编辑");
    await editPrompt("刷新失败后继续输入");
    check(input.value === "刷新失败后继续输入", "刷新失败后仍可输入并留存草稿");
    await act(async () => { button("重试").click(); });
    await respond(failedRefreshIndex + 1, panel.serverNode());
    check(input.value === "刷新失败后继续输入" && document.activeElement === input, "重试成功后保留新输入与焦点");
  } finally {
    await act(async () => { root.render(null); });
    window.setInterval = originalSetInterval;
    window.clearInterval = originalClearInterval;
  }
}

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
  await testDraftPromotion();
  await testAutosaveRefresh();
  await act(async () => { root.unmount(); });
  document.body.dataset.testResult = "passed";
} catch (error) {
  document.body.dataset.testResult = "failed";
  const result = document.createElement("pre");
  result.textContent = error instanceof Error ? error.stack ?? error.message : String(error);
  document.body.append(result);
}
