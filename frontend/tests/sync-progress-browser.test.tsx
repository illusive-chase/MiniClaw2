import { act } from "react";
import { createRoot } from "react-dom/client";
import { GlobalSettingsModal } from "../src/components/GlobalSettingsModal";
import type { GlobalState } from "../src/types";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

function check(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function deferred<Value>() {
  let resolve!: (value: Value) => void;
  const promise = new Promise<Value>((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

const state: GlobalState = {
  config_path: "/store/global.json",
  defaults: { default_model_preset_id: "codex", auto_commit: false, concurrency: 1 },
  code_review: { model_preset_id: "codex" },
  tool_requests: { timeout_seconds: 30, timeout_action: "reject" },
  model_presets: [],
  sync: {
    configured: true, remote_url: "/remote.git", status: "changed", changed: true,
    machine_id: "local", machine_label: "本机", hostname_mismatch: false, privacy_notice: "私有远端",
  },
};
const intervals = new Map<number, () => void>();
let intervalId = 0;
window.setInterval = (handler: TimerHandler) => {
  check(typeof handler === "function", "轮询应使用函数回调");
  intervals.set(++intervalId, handler as () => void);
  return intervalId;
};
window.clearInterval = (identifier?: number) => { intervals.delete(identifier!); };
let now = 10000;
Date.now = () => now;
let syncRequest = deferred<Response>();
let blockedStatus: ReturnType<typeof deferred<Response>> | null = null;
let statusSignal: AbortSignal | null | undefined;
let statusRequests = 0;
let pollingFailure = false;
let phase = "fetching";
let detail = "正在拉取远端元数据";
const payload = () => ({
  state: "waiting_for_idle", target: 17, minimum: 14, detail: "同步中",
  sync_progress: { phase, detail, started_at: 10, phase_started_at: 10 },
});
globalThis.fetch = async (input, init) => {
  const path = String(input);
  if (path === "/self-update") return Response.json({ is_repo: false });
  if (path === "/global-state/sync" && init?.method === "POST") return syncRequest.promise;
  if (path === "/migrations/status") {
    statusRequests += 1;
    statusSignal = init?.signal;
    if (pollingFailure) throw new Error("暂时无法获取阶段");
    return blockedStatus ? blockedStatus.promise : Response.json(payload());
  }
  throw new Error(`不应访问：${path}`);
};

const container = document.createElement("div");
document.body.append(container);
const root = createRoot(container);
const changed: GlobalState[] = [];
const onChanged = (next: GlobalState) => { changed.push(next); };
const onClose = () => {};
const render = async (open: boolean) => {
  await act(async () => { root.render(<GlobalSettingsModal open={open} state={state} onClose={onClose} onChanged={onChanged} />); });
};
const clickSync = async () => {
  const button = Array.from(container.querySelectorAll("button")).find((candidate) => candidate.textContent === "Sync now");
  check(button, "应显示同步按钮");
  await act(async () => { button.click(); });
};
const tick = async () => {
  now += 1500;
  await act(async () => { for (const callback of intervals.values()) callback(); });
};
const waitForStatus = async (text: string) => {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (container.querySelector('[role="status"]')?.textContent?.includes(text)) return;
    await act(async () => { await new Promise<void>((resolve) => window.setTimeout(resolve, 0)); });
  }
  check(false, `同步状态应包含「${text}」，实际为：${container.querySelector('[role="status"]')?.textContent}`);
};

async function main() {
  await render(true);
  check(statusRequests === 0 && intervals.size === 0, "打开面板不应发起同步轮询");
  await clickSync();
  await waitForStatus(detail);
  check(container.querySelector('[role="status"]')?.textContent?.includes(detail), "同步期间应显示后端阶段");
  check(intervals.size === 1, "只应启动一个轮询器");
  pollingFailure = true;
  await tick();
  check(!container.textContent?.includes("暂时无法获取阶段"), "轮询失败不得伪装成同步失败");
  check(container.textContent?.includes("已用时 1 秒"), "轮询失败时仍应更新等待时长");
  pollingFailure = false;
  phase = "normalizing_remote";
  detail = "正在读取并迁移远端快照";
  await tick();
  await waitForStatus(detail);
  check(container.querySelector('[role="status"]')?.textContent?.includes(detail), "轮询失败后应继续重试并更新阶段");
  blockedStatus = deferred<Response>();
  await tick();
  const before = statusRequests;
  await tick();
  check(statusRequests === before, "慢状态请求不得重叠");
  const closingSignal = statusSignal;
  await render(false);
  check(intervals.size === 0 && closingSignal?.aborted, "关闭面板应停止轮询并中止状态请求");
  await act(async () => { blockedStatus!.resolve(Response.json(payload())); });
  blockedStatus = null;
  await render(true);
  check(intervals.size === 1, "重新打开仍在同步的面板应恢复轮询");
  await waitForStatus("已用时 6 秒");
  const reopenedStatus = container.querySelector('[role="status"]')?.textContent;
  check(reopenedStatus?.includes("已用时 6 秒"), `重新打开应显示后端开始时间对应的总耗时，实际为：${reopenedStatus}`);
  await act(async () => { syncRequest.resolve(Response.json(state)); });
  check(changed.length === 1 && intervals.size === 0, "同步成功应通知父组件并清理定时器");
  check(!container.querySelector('[role="status"]'), "同步完成后不应残留运行阶段");

  syncRequest = deferred<Response>();
  await clickSync();
  await act(async () => { syncRequest.resolve(Response.json({ detail: "推送失败，请重试" }, { status: 409 })); });
  check(container.textContent?.includes("推送失败，请重试"), "真实同步错误应保留");
  check(intervals.size === 0, "同步失败也应清理定时器");

  syncRequest = deferred<Response>();
  blockedStatus = deferred<Response>();
  await clickSync();
  const unmountSignal = statusSignal;
  await act(async () => { root.unmount(); });
  check(intervals.size === 0 && unmountSignal?.aborted, "卸载组件应清理轮询和在途请求");
  await act(async () => {
    blockedStatus!.resolve(Response.json(payload()));
    syncRequest.resolve(Response.json(state));
  });
}

main().then(() => { document.body.dataset.testResult = "passed"; }).catch((error) => {
  document.body.dataset.testResult = "failed";
  const output = document.createElement("pre");
  output.textContent = String(error?.stack ?? error);
  document.body.append(output);
});
