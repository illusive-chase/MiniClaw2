/* Presence timing in a real browser: what a user would see if it broke.
 *
 *   - a closed modal stays on screen just long enough to animate out, takes
 *     no clicks while it does, and is then gone;
 *   - reopening mid-exit keeps the same panel instead of blinking;
 *   - Escape closes only the topmost modal;
 *   - focus returns to whatever opened the dialog;
 *   - a collapse that starts open does not animate, a toggle does.
 */
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { ModalShell } from "../src/components/ModalShell";
import { Collapse } from "../src/components/Collapse";
import { EXIT_MS } from "../src/motion";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const container = document.createElement("div");
document.body.append(container);
const root = createRoot(container);

function check(value: unknown, message: string): asserts value {
  if (!value) throw new Error(message);
}
const wait = (ms: number) => act(() => new Promise<void>((resolve) => setTimeout(resolve, ms)));
const dialogs = () => [...document.querySelectorAll<HTMLElement>('[role="dialog"]')];

let setOuter: (open: boolean) => void = () => {};
let setInner: (open: boolean) => void = () => {};
let setSection: (open: boolean) => void = () => {};
function Harness() {
  const [outer, setOuterOpen] = useState(false);
  const [inner, setInnerOpen] = useState(false);
  const [section, setSectionOpen] = useState(true);
  setOuter = setOuterOpen;
  setInner = setInnerOpen;
  setSection = setSectionOpen;
  return (
    <>
      <button id="opener" type="button">open</button>
      <ModalShell open={outer} onClose={() => setOuterOpen(false)} label="outer" panelClassName="w-40 h-20">
        <input id="outer-field" />
      </ModalShell>
      <ModalShell open={inner} onClose={() => setInnerOpen(false)} label="inner" panelClassName="w-20 h-10" zClassName="z-50">
        <button type="button">inner</button>
      </ModalShell>
      <Collapse open={section}>
        <div id="section-body" style={{ height: 40 }}>body</div>
      </Collapse>
    </>
  );
}

async function run() {
  await act(async () => root.render(<Harness />));

  /* A section open at mount is not animated. */
  const frame = document.querySelector<HTMLElement>(".mc-collapse");
  check(frame?.dataset.instant === "true" && frame.dataset.open === "true", "初始展开的折叠区不应播放动画");

  /* Open: mounted with an entry phase, focus moves inside. */
  document.querySelector<HTMLButtonElement>("#opener")!.focus();
  await act(async () => setOuter(true));
  await wait(20);
  check(dialogs().length === 1, "打开后应挂载对话框");
  check(dialogs()[0].dataset.phase === "enter", "进场阶段应为 enter");
  check(document.activeElement?.id === "outer-field", "打开后焦点应进入对话框");

  /* Escape closes only the top modal. */
  await act(async () => setInner(true));
  await wait(20);
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  });
  await wait(EXIT_MS.modal + 60);
  check(dialogs().length === 1 && dialogs()[0].getAttribute("aria-label") === "outer", "Esc 只应关闭最上层对话框");

  /* Close: still mounted during the exit, not clickable, then gone. */
  await act(async () => setOuter(false));
  const leaving = dialogs()[0];
  check(leaving?.dataset.phase === "exit", "关闭后应进入退场阶段而不是立即卸载");
  const scrim = leaving.parentElement!;
  check(getComputedStyle(scrim).pointerEvents === "none", "退场期间遮罩不应拦截点击");
  await wait(EXIT_MS.modal + 60);
  check(dialogs().length === 0, "退场结束后应卸载对话框");
  check(document.activeElement?.id === "opener", "关闭后焦点应回到触发元素");

  /* Reopen mid-exit: the same element comes back. */
  await act(async () => setOuter(true));
  await wait(20);
  const first = dialogs()[0];
  await act(async () => setOuter(false));
  await wait(EXIT_MS.modal / 2);
  await act(async () => setOuter(true));
  await wait(EXIT_MS.modal + 60);
  check(dialogs()[0] === first && first.dataset.phase === "enter", "退场途中重新打开应保留同一面板");
  await act(async () => setOuter(false));
  await wait(EXIT_MS.modal + 60);

  /* A toggled collapse animates and unmounts after its exit. */
  await act(async () => setSection(false));
  check(document.querySelector("#section-body"), "收起动画期间内容应仍在");
  await wait(EXIT_MS.panel + 60);
  check(!document.querySelector("#section-body"), "收起结束后内容应卸载");
  await act(async () => setSection(true));
  await wait(40);
  const reopened = document.querySelector<HTMLElement>(".mc-collapse");
  check(reopened?.dataset.instant === "false" && reopened.dataset.open === "true", "再次展开应走动画路径");

  document.body.dataset.testResult = "passed";
}
run().catch((error) => {
  document.body.dataset.testResult = "failed";
  const pre = document.createElement("pre");
  pre.textContent = String(error.stack ?? error);
  document.body.append(pre);
});
