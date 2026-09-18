import { act } from "react";
import { createRoot } from "react-dom/client";
import { NewProjectModal } from "../src/components/NewProjectModal";
import { RemoteAccessModal } from "../src/components/RemoteAccessModal";
import type { ModelPreset, SessionInfo } from "../src/types";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const container = document.createElement("div");
document.body.append(container);
const root = createRoot(container);
const presets: ModelPreset[] = [{ id: "gpt-5.6", provider: "codex", model: "gpt-5.6", label: "Codex", status: "active", is_default: true, description: "" }];
const session = { id: "remote", bound_here: true, persistence_mode: "remote", remote: { target_id: "test", root_path: "/srv/test" } } as SessionInfo;
const requests: Array<{ url: string; method: string; body: Record<string, any> }> = [];
window.fetch = async (url, init) => {
  requests.push({ url: String(url), method: init?.method ?? "GET", body: JSON.parse(String(init?.body)) });
  return new Response(JSON.stringify(session), { status: 200, headers: { "Content-Type": "application/json" } });
};

function check(value: unknown, message: string): asserts value {
  if (!value) throw new Error(message);
}
async function click(text: string) {
  const button = [...document.querySelectorAll("button")].find((item) => item.textContent?.trim() === text);
  check(button && !button.disabled, `按钮不可用：${text}`);
  await act(async () => button.click());
}
async function input(placeholder: string, value: string) {
  const element = document.querySelector<HTMLInputElement>(`input[placeholder="${placeholder}"]`);
  check(element, `输入框不存在：${placeholder}`);
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function select(value: string) {
  const element = [...document.querySelectorAll("select")].find((item) => [...item.options].some((option) => option.value === value));
  check(element, `选项不存在：${value}`);
  await act(async () => { element.value = value; element.dispatchEvent(new Event("change", { bubbles: true })); });
}

async function run() {
  await act(async () => root.render(<NewProjectModal open modelPresets={presets} defaults={null} onCancel={() => {}} onCreated={() => {}} />));
  await click("远端目录");
  await input("autodl-a100", "test");
  await input("user@gpu-box", "gpu-box");
  await input("/root/autodl-tmp/project", "/srv/test");
  await select("cloned");
  await input("git@host:owner/repo.git", "git@host:owner/repo.git");
  const checkbox = [...document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].find((item) => item.parentElement?.textContent?.includes("Codex"));
  check(checkbox && !checkbox.checked, "实验开关必须默认关闭");
  await act(async () => checkbox.click());
  await select("externalSandbox");
  check(document.body.textContent?.includes("不提供进程级文件隔离"), "缺少外部沙箱警告");
  await click("Create project");
  check(requests[0].body.remote_initialization === "cloned", "未提交克隆模式");
  check(requests[0].body.remote_access.codex_remote_experimental, "未提交实验开关");
  check(requests[0].body.remote_access.sandbox === "externalSandbox", "未提交沙箱选择");
  await act(async () => root.render(<RemoteAccessModal session={{ ...session, remote_access: {
    ssh_target: "gpu-box", codex_remote_experimental: true, sandbox: "externalSandbox", codex_path: "/opt/codex/bin/codex",
  } }} onClose={() => {}} onSaved={() => {}} />));
  await click("验证并保存");
  check(requests[1].method === "PUT" && requests[1].url.endsWith("/remote-access"), "已有绑定没有更新配置");
  check(requests[1].body.codex_path === "/opt/codex/bin/codex", "执行器路径未保存");
  const dialog = document.querySelector('[role="dialog"]')!;
  const rect = dialog.getBoundingClientRect();
  check(rect.left >= 0 && rect.right <= innerWidth, "配置界面横向溢出");
  check(rect.top >= 0 && rect.bottom <= innerHeight, "配置界面纵向溢出");
  document.body.dataset.testResult = "passed";
}
run().catch((error) => {
  document.body.dataset.testResult = "failed";
  const pre = document.createElement("pre");
  pre.textContent = String(error.stack ?? error);
  document.body.append(pre);
});
