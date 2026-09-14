import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { TagEditPopover } from "../src/components/TagEditPopover";
import { ProjectPanel, type ProjectPanelProps } from "../src/panel/ProjectPanel";
import type { SessionInfo, Tag } from "../src/types";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const tags: Tag[] = [
  { id: "work", name: "工作", color: "coral", created_at: 0 },
  { id: "research", name: "研究", color: "sage", created_at: 0 },
];
const container = document.createElement("div");
const outside = document.createElement("button");
document.body.append(container, outside);
const root = createRoot(container);
const dialog = () => document.querySelector('[role="dialog"]');

function check(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function deferred<Value>() {
  let resolve!: (value: Value) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Value>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function editName(value: string) {
  const label = dialog()?.querySelector('[title="双击重命名"]');
  check(label, "应显示可重命名的标签");
  await act(async () => { label.dispatchEvent(new MouseEvent("dblclick", { bubbles: true })); });
  const input = dialog()?.querySelector<HTMLInputElement>('input[aria-label^="重命名 tag"]');
  check(input, "应打开重命名输入框");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  return input;
}

async function clickOutside() {
  await act(async () => {
    const allowed = outside.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    if (allowed) outside.focus();
    outside.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    outside.click();
  });
}

async function pressKey(target: Element, key: string) {
  await act(async () => { target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true })); });
}

async function mountEditor(onRenameTag: (id: string, name: string) => Promise<void>) {
  await act(async () => { root.render(null); });
  function Editor() {
    const [open, setOpen] = useState(true);
    return open ? <TagEditPopover
      anchor={container}
      tags={tags}
      selectedIds={[tags[0].id]}
      onClose={() => setOpen(false)}
      onApply={async () => {}}
      onCreateTag={async () => tags[0]}
      onRenameTag={onRenameTag}
    /> : null;
  }
  await act(async () => { root.render(<Editor />); });
}

async function testRenameDismissal() {
  const saved = deferred<void>();
  const calls: string[][] = [];
  await mountEditor(async (id, name) => { calls.push([id, name]); await saved.promise; });
  const input = await editName("  新工作  ");
  await clickOutside();
  check(JSON.stringify(calls) === JSON.stringify([["work", "新工作"]]), "外点必须提交修剪后的名称");
  check(dialog(), "保存完成之前应保留编辑器");
  await act(async () => { input.dispatchEvent(new FocusEvent("focusout", { bubbles: true })); });
  await clickOutside();
  check(calls.length === 1, "失焦和连续外点不得重复提交");
  await act(async () => { saved.resolve(); });
  check(!dialog(), "保存成功后应关闭编辑器");

  const failed = deferred<void>();
  await mountEditor(async () => { await failed.promise; });
  await editName("新工作");
  await clickOutside();
  await act(async () => { failed.reject(new Error("保存失败，请重试")); });
  check(dialog()?.textContent?.includes("保存失败，请重试"), "保存失败必须显示错误而不是关闭弹层");
  check(dialog()?.querySelector<HTMLInputElement>('input[aria-label^="重命名 tag"]')?.value === "新工作", "保存失败应保留草稿");

  let cancelledCalls = 0;
  await mountEditor(async () => { cancelledCalls += 1; });
  const cancelledInput = await editName("取消的名称");
  await pressKey(cancelledInput, "Escape");
  check(dialog() && !dialog()?.querySelector('input[aria-label^="重命名 tag"]'), "Escape 应先取消重命名");
  await clickOutside();
  check(!dialog() && cancelledCalls === 0, "Escape 取消的草稿不得在关闭时提交");

  for (const name of ["工作", "   ", "研究"]) {
    await mountEditor(async () => { throw new Error("无效或未修改的名称不应提交"); });
    await editName(name);
    await clickOutside();
    check(!dialog(), "无效或未修改的名称应取消编辑并关闭");
  }

  const blurSave = deferred<void>();
  let blurCalls = 0;
  await mountEditor(async () => { blurCalls += 1; await blurSave.promise; });
  const blurInput = await editName("失焦名称");
  await act(async () => { blurInput.blur(); });
  check(blurCalls === 1, "普通失焦仍应提交重命名");
  await act(async () => { blurSave.resolve(); });
  check(dialog(), "普通失焦不应关闭整个弹层");

  const enterSave = deferred<void>();
  let enterCalls = 0;
  await mountEditor(async () => { enterCalls += 1; await enterSave.promise; });
  const enterInput = await editName("回车名称");
  await pressKey(enterInput, "Enter");
  await clickOutside();
  check(enterCalls === 1, "回车后外点不得重复提交");
  await act(async () => { enterSave.resolve(); });
  await clickOutside();
  check(!dialog(), "回车保存后应能正常关闭弹层");
}

async function testDeleteUsesCurrentSession() {
  await act(async () => { root.render(null); });
  const deletion = deferred<Response>();
  const originalFetch = window.fetch;
  window.fetch = async (url, options) => {
    if (url === "/tags" && !options?.method) return Response.json(tags);
    check(url === "/tags/work" && options?.method === "DELETE", "只应请求删除目标标签");
    return deletion.promise;
  };
  const original = {
    id: "project", concurrency: 1, tag_ids: ["work", "research"], hosts: [],
    node_positions: { node: { x: 10, y: 20, space: "canvas" } },
  } as unknown as SessionInfo;
  let current = original;
  const noop = () => {};
  const props: ProjectPanelProps = {
    session: original,
    onSessionChange: (update) => {
      check(typeof update === "function", "删除标签必须使用函数式会话更新");
      current = update(current)!;
    },
    modelPresets: [], contextSpace: null, contextSpaceLoading: false,
    contextSpaceSaving: false, contextSpaceError: null, settingsSaving: false, settingsError: null,
    onSelectContextBinding: noop, onPreferredLanguageChange: noop, onConcurrencyChange: noop,
    onStartBlankDirection: noop, onContextInit: noop, onContextRefresh: noop, onContextCancel: noop,
    onTogglePlanspaceVisibility: noop, onDeletePlanspace: async () => {},
    newDirectionRequestVersion: 0, onNewDirectionRequestHandled: noop,
  };
  try {
    await act(async () => { root.render(<ProjectPanel {...props} />); });
    const trigger = container.querySelector<HTMLButtonElement>('button[title="编辑 tag"]');
    check(trigger, "应显示标签编辑入口");
    await act(async () => { trigger.click(); });
    const remove = dialog()?.querySelector<HTMLButtonElement>('button[title="删除 tag（影响所有使用该 tag 的项目）"]');
    check(remove, "应显示删除标签按钮");
    await act(async () => { remove.click(); });
    await act(async () => { remove.click(); });
    current = {
      ...original, concurrency: 4, preferred_language: "zh-CN", tag_ids: ["work", "research", "new"],
      node_positions: { node: { x: 800, y: 900, space: "canvas" } },
    };
    const currentPositions = current.node_positions;
    await act(async () => {
      root.render(<ProjectPanel {...props} session={current} />);
      deletion.resolve(new Response(null, { status: 204 }));
    });
    check(current.concurrency === 4 && current.preferred_language === "zh-CN", "删除标签不得回滚并发更新的项目设置");
    check(current.node_positions === currentPositions, "删除标签不得重新载入旧布局");
    check(JSON.stringify(current.tag_ids) === JSON.stringify(["research", "new"]), "删除标签应保留请求期间新增的标签");
  } finally {
    window.fetch = originalFetch;
  }
}

try {
  await testRenameDismissal();
  await testDeleteUsesCurrentSession();
  await act(async () => { root.unmount(); });
  document.body.setAttribute("data-test-result", "passed");
} catch (error) {
  document.body.setAttribute("data-test-result", "failed");
  document.body.textContent = error instanceof Error ? error.stack ?? error.message : String(error);
}
