import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const browserBin = process.env.BROWSER_BIN;
assert.ok(browserBin, "请设置 BROWSER_BIN，指向 Chrome、Chromium 或 Edge 可执行文件");
const temporary = await mkdtemp(join(tmpdir(), "miniclaw2-viewport-browser-"));
let browser;
const pending = new Map();
try {
  const bundle = await build({
    entryPoints: [new URL("./canvas-viewport-browser.test.tsx", import.meta.url).pathname],
    bundle: true, format: "esm", write: false, logLevel: "warning", jsx: "automatic", loader: { ".css": "empty" },
  });
  const sourceCss = await readFile(new URL("../src/index.css", import.meta.url), "utf8");
  const flowCss = await readFile(new URL("../node_modules/reactflow/dist/style.css", import.meta.url), "utf8");
  const css = await postcss([tailwindcss(new URL("../tailwind.config.ts", import.meta.url).pathname)]).process(sourceCss, { from: undefined });
  const html = join(temporary, "test.html");
  await writeFile(html, `<!doctype html><html><head><meta charset="utf-8"><style>${flowCss}\n${css.css}\nhtml,body,#root{width:100%;height:100%;margin:0}</style></head><body><div id="root"></div><script type="module">${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script></body></html>`);
  browser = spawn(browserBin, [
    "--headless=new", "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
    "--remote-debugging-pipe", `--user-data-dir=${join(temporary, "profile")}`, "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"] });
  let diagnostics = "";
  browser.stderr.on("data", (data) => { diagnostics += data; });
  let buffer = "";
  const errors = [];
  browser.stdio[4].on("data", (data) => {
    buffer += data;
    let separator;
    while ((separator = buffer.indexOf("\0")) >= 0) {
      const payload = JSON.parse(buffer.slice(0, separator));
      buffer = buffer.slice(separator + 1);
      if (payload.method === "Runtime.exceptionThrown") errors.push(payload.params.exceptionDetails);
      const request = pending.get(payload.id);
      if (!request) continue;
      pending.delete(payload.id);
      clearTimeout(request.timer);
      if (payload.error) request.reject(new Error(JSON.stringify(payload.error)));
      else request.resolve(payload.result);
    }
  });
  let nextId = 0;
  function command(method, params = {}, sessionId) {
    return new Promise((resolve, reject) => {
      const id = ++nextId;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`浏览器命令超时：${method}\n${diagnostics}`));
      }, 15000);
      pending.set(id, { resolve, reject, timer });
      browser.stdio[3].write(JSON.stringify({ id, method, params, sessionId }) + "\0");
    });
  }
  await once(browser, "spawn");
  const { targetId } = await command("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await command("Target.attachToTarget", { targetId, flatten: true });
  const page = (method, params) => command(method, params, sessionId);
  const evaluate = async (expression) => {
    const result = await page("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    assert.equal(result.exceptionDetails, undefined, JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const eventually = async (expression) => {
    for (let attempt = 0; attempt < 150; attempt += 1) {
      assert.deepEqual(errors, []);
      const value = await evaluate(`Boolean(${expression})`);
      if (value) return value;
      await delay(100);
    }
    throw new Error(`等待画布条件超时：${expression}`);
  };
  await page("Runtime.enable");
  await page("Emulation.setDeviceMetricsOverride", { width: 1100, height: 800, deviceScaleFactor: 1, mobile: false });
  await page("Page.navigate", { url: pathToFileURL(html).href });
  await eventually("window.canvasTest && document.querySelector('.react-flow__node-planspaceLane')");
  await delay(400);
  const initial = await evaluate(`({
    total: canvasTest.total, rendered: document.querySelectorAll('.react-flow__node').length,
    source: !!document.querySelector('[data-id="source"]'), target: !!document.querySelector('[data-id="target"]'),
    edges: [...document.querySelectorAll('.react-flow__edge')].map(element => element.dataset.testid)
  })`);
  assert.ok(initial.total > 1900);
  assert.ok(initial.rendered < 50, JSON.stringify(initial));
  assert.equal(initial.source, false);
  assert.equal(initial.target, false);
  assert.equal(await evaluate("!!document.querySelector('[data-id=drop]')"), true, "部分进入视口的节点仍应挂载");
  assert.ok(initial.edges.some((id) => id.includes("source") && id.includes("target")), "首次打开时，两端均在视口外的跨视口边仍应渲染");
  assert.ok(initial.edges.length < 50, "透明的来源边不应占用 DOM");
  const bounds = (id) => evaluate(`(() => {
    const rect = document.querySelector('[data-id="${id}"]').getBoundingClientRect();
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
  })()`);
  const mouse = (type, point, extra = {}) => page("Input.dispatchMouseEvent", { type, ...point, ...extra });
  const click = async (point, modifiers = 0) => {
    await mouse("mouseMoved", point);
    await mouse("mousePressed", point, { button: "left", buttons: 1, clickCount: 1, modifiers });
    await mouse("mouseReleased", point, { button: "left", buttons: 0, clickCount: 1, modifiers });
    await delay(100);
  };
  const center = (rect) => ({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 });
  let version = 0;
  const focus = async (nodeId) => {
    await evaluate(`canvasTest.patch({centerOnNodeRequest:{nodeId:${JSON.stringify(nodeId)},version:${++version}}})`);
    await eventually(`document.querySelector('[data-id="${nodeId}"]')`);
    await delay(350);
  };
  const transform = () => evaluate("document.querySelector('.react-flow__viewport').style.transform");
  const fill = async (selector, value) => {
    await evaluate(`(() => {
      const field = document.querySelector(${JSON.stringify(selector)});
      field.focus(); field.select();
    })()`);
    await page("Input.insertText", { text: value });
  };
  for (const kind of ["ask_user", "permission"]) {
    await focus("source");
    await evaluate(`canvasTest.openGate(${JSON.stringify(kind)})`);
    await eventually("document.querySelector('[data-id=source] input')");
    if (kind === "ask_user") {
      await evaluate("[...document.querySelectorAll('[data-id=source] button')].find(button => button.textContent === '保留选项').click()");
      await fill('[data-id=source] input[placeholder="输入你的回答"]', "平移后仍保留的回答");
    } else {
      await fill("[data-id=source] textarea", '{"command":"git status --short"}');
      await fill("[data-id=source] input", "平移后仍保留的备注");
    }
    await evaluate("window.gateEditors = [...document.querySelectorAll('[data-id=source] input, [data-id=source] textarea')]; undefined");
    const draft = await evaluate("gateEditors.map(field => field.value)");
    const gateRect = await bounds("source");
    const panStart = { x: 1050, y: 700 };
    const panEnd = { x: panStart.x, y: panStart.y - gateRect.y - gateRect.height - 4 };
    await mouse("mouseMoved", panStart);
    await mouse("mousePressed", panStart, { button: "right", buttons: 2, clickCount: 1 });
    await mouse("mouseMoved", panEnd, { button: "right", buttons: 2 });
    await mouse("mouseReleased", panEnd, { button: "right", buttons: 0, clickCount: 1 });
    await delay(200);
    assert.equal(await evaluate("gateEditors.every(field => field.isConnected)"), true, `${kind}：卡片移出视口时不能卸载表单`);
    const outsideRect = await bounds("source");
    assert.ok(outsideRect.y + outsideRect.height < 0, "卡片本体应已移出视口上方");
    assert.equal(await evaluate(`(() => {
      const rect = document.querySelector('[data-id=source] .nodrag.absolute').getBoundingClientRect();
      return rect.top >= 0 && rect.bottom < 800;
    })()`), true, "卡片下方的交互表单仍应完整可见");
    await focus("target");
    assert.equal(await evaluate("gateEditors.every(field => field.isConnected)"), true, `${kind}：表单完全离屏也不能丢失草稿`);
    await focus("source");
    assert.deepEqual(await evaluate("[...document.querySelectorAll('[data-id=source] input, [data-id=source] textarea')].map(field => field.value)"), draft);
    const submitLabel = kind === "ask_user" ? "Send" : "Allow";
    await evaluate(`[...document.querySelectorAll('[data-id=source] button')].find(button => button.textContent === ${JSON.stringify(submitLabel)}).click()`);
    const response = await evaluate("canvasTest.results.responses.at(-1)");
    if (kind === "ask_user") {
      assert.deepEqual(response, {
        id: "ask-gate", payload: { allow: true, response: { answers: {
          choice: { answers: ["保留选项"] }, answer: { answers: ["平移后仍保留的回答"] },
        } } },
      });
    } else {
      assert.deepEqual(response, {
        id: "permission-gate", payload: {
          allow: true, scope: null, interrupt: false, message: "平移后仍保留的备注",
          updated_input: { command: "git status --short" },
        },
      });
    }
    await evaluate("canvasTest.closeGate()");
    await eventually("!document.querySelector('[data-id=source] input')");
    await focus("target");
    await eventually("!document.querySelector('[data-id=source]')");
    assert.ok(await evaluate("document.querySelectorAll('.react-flow__node').length < 50"), "交互结束后应恢复视口裁剪");
  }
  await focus("source");
  assert.equal(await evaluate("!!document.querySelector('[data-id=target]')"), false);
  const sourceRect = await bounds("source");
  assert.ok(Math.abs(center(sourceRect).x - 550) < 3);
  await click(center(sourceRect));
  await page("Input.dispatchKeyEvent", { type: "keyDown", key: "Shift", code: "ShiftLeft", windowsVirtualKeyCode: 16, modifiers: 8 });
  await click(center(await bounds("drop")), 8);
  await page("Input.dispatchKeyEvent", { type: "keyUp", key: "Shift", code: "ShiftLeft", windowsVirtualKeyCode: 16 });
  await eventually("canvasTest.results.selections.at(-1)?.includes('source') && canvasTest.results.selections.at(-1)?.includes('drop')");
  await click({ x: 1050, y: 40 });
  const dropRect = await bounds("drop");
  const start = { x: sourceRect.x - 15, y: sourceRect.y - 15 };
  const end = { x: dropRect.x + dropRect.width + 15, y: dropRect.y + dropRect.height + 15 };
  await mouse("mousePressed", start, { button: "left", buttons: 1, clickCount: 1 });
  await mouse("mouseMoved", end, { buttons: 1 });
  await mouse("mouseReleased", end, { button: "left", buttons: 0, clickCount: 1 });
  await eventually("canvasTest.results.selections.at(-1)?.includes('source') && canvasTest.results.selections.at(-1)?.includes('drop')");
  await eventually("document.querySelector('.react-flow__nodesselection-rect')");
  await evaluate("canvasTest.patch({nodes:canvasTest.nodes.map(node => node.id === 'source' ? {...node, rev:2, summary:'更新后的节点'} : node)})");
  await eventually("document.querySelector('[data-id=source]').textContent.includes('更新后的节点')");
  assert.ok(await evaluate("canvasTest.results.selections.at(-1).includes('source') && canvasTest.results.selections.at(-1).includes('drop')"), "节点更新保留多选");
  const menuPoint = center(await bounds("source"));
  await mouse("mousePressed", menuPoint, { button: "right", buttons: 2, clickCount: 1 });
  await mouse("mouseReleased", menuPoint, { button: "right", buttons: 0, clickCount: 1 });
  await eventually("canvasTest.results.menus === 1");
  await click({ x: 1050, y: 40 });
  await eventually("!document.querySelector('.react-flow__nodesselection-rect') && !document.querySelector('[data-id=source]').classList.contains('selected')");
  const beforePan = await transform();
  const panStart = center(await bounds("source"));
  await mouse("mouseMoved", panStart);
  await mouse("mousePressed", panStart, { button: "right", buttons: 2, clickCount: 1 });
  await delay(50);
  await mouse("mouseMoved", { x: panStart.x + 120, y: panStart.y + 60 }, { button: "right", buttons: 2 });
  await delay(50);
  await mouse("mouseReleased", { x: panStart.x + 120, y: panStart.y + 60 }, { button: "right", buttons: 0, clickCount: 1 });
  await delay(150);
  assert.notEqual(await transform(), beforePan, "未选中节点上右键拖动仍能平移");
  assert.equal(await evaluate("canvasTest.results.menus"), 1, "平移不应弹出菜单");
  const savedViewport = await evaluate("localStorage.getItem('miniclaw2.canvas-viewport.v1:viewport-test')");
  const savedTransform = await transform();
  await evaluate("canvasTest.remount()");
  await eventually("document.querySelector('[data-id=source]')");
  await delay(400);
  assert.equal(await transform(), savedTransform, "重新挂载画布恢复手动视口");
  await focus("target");
  assert.equal(await evaluate("localStorage.getItem('miniclaw2.canvas-viewport.v1:viewport-test')"), savedViewport, "定位不覆盖手动视口");
  await eventually("!document.querySelector('[data-id=source]')");
  assert.ok(await evaluate("!!document.querySelector('[data-testid=\"rf__edge-dep:source->target\"] path')"));
  await focus("source");
  await click({ x: 1050, y: 40 });
  const button = await evaluate(`(() => {
    const rect = document.querySelector('[data-id="source"] button[title*="长按"]').getBoundingClientRect();
    return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
  })()`);
  await mouse("mouseMoved", button);
  await mouse("mousePressed", button, { button: "left", buttons: 1, clickCount: 1 });
  await delay(300);
  const dropCenter = center(await bounds("drop"));
  await mouse("mouseMoved", dropCenter, { buttons: 1 });
  await mouse("mouseReleased", dropCenter, { button: "left", buttons: 0, clickCount: 1 });
  await eventually("canvasTest.results.connections.some(pair => pair[0] === 'drop' && pair[1] === 'source')");
  await evaluate("canvasTest.patch({nodes:canvasTest.nodes.map(node => node.id === 'target' ? {...node, scheduled_deps:[]} : node)})");
  await mouse("mouseMoved", button);
  await mouse("mousePressed", button, { button: "left", buttons: 1, clickCount: 1 });
  await delay(300);
  await focus("target");
  await eventually("!document.querySelector('[data-id=source]')");
  const farCenter = center(await bounds("target"));
  await mouse("mouseMoved", farCenter, { buttons: 1 });
  await mouse("mouseReleased", farCenter, { button: "left", buttons: 0, clickCount: 1 });
  await eventually("canvasTest.results.connections.some(pair => pair[0] === 'target' && pair[1] === 'source')");
  await focus("scale-4-410");
  await eventually("!document.querySelector('[data-id=source]')");
  assert.ok(await evaluate("document.querySelectorAll('.react-flow__node').length < 50"));
  await evaluate("canvasTest.patch({hiddenPlanspaceIds:['scale-4']})");
  await eventually("!document.querySelector('[data-id=\"scale-4-410\"]')");
  await evaluate("canvasTest.patch({hiddenPlanspaceIds:[]})");
  await focus("scale-4-410");
  await mouse("mouseMoved", center(await bounds("scale-4-410")));
  await eventually("document.querySelector('.react-flow__edge-loads')");
  await mouse("mouseMoved", { x: 1050, y: 40 });
  await eventually("!document.querySelector('.react-flow__edge-loads')");
  await evaluate(`canvasTest.patch({
    nodes:canvasTest.nodes.map(node => ['source','drop'].includes(node.id) ? {...node,template_instance_id:'pair'} : node),
    collapsedTemplateInstanceIds:['pair'], centerOnNodeRequest:{nodeId:'source',version:${++version}}
  })`);
  await eventually("document.querySelector('[data-id=\"tplbox:pair\"]') && !document.querySelector('[data-id=source]')");
  await delay(300);
  await evaluate("canvasTest.patch({collapsedTemplateInstanceIds:[]})");
  await focus("source");
  const beforeFit = await transform();
  await evaluate("document.querySelector('.react-flow__controls-fitview').click()");
  await delay(400);
  assert.notEqual(await transform(), beforeFit, "适配视图不依赖离屏节点挂载");
  const beforeZoom = await transform();
  await mouse("mouseWheel", { x: 550, y: 400 }, { deltaX: 0, deltaY: -120, modifiers: 2 });
  await delay(200);
  assert.notEqual(await transform(), beforeZoom, "Ctrl 滚轮缩放仍可用");
  await evaluate(`window.frameIntervals=[];window.collectFrames=true;window.lastFrame=null;
    requestAnimationFrame(function record(now){
      if(window.lastFrame !== null) window.frameIntervals.push(now-window.lastFrame);
      window.lastFrame=now;if(window.collectFrames)requestAnimationFrame(record);
    })`);
  let peakNodes = 0;
  for (let step = 0; step < 12; step += 1) {
    await mouse("mouseWheel", { x: 550, y: 40 }, { deltaX: 120, deltaY: 0 });
    await delay(30);
    peakNodes = Math.max(peakNodes, await evaluate("document.querySelectorAll('.react-flow__node').length"));
  }
  const frameIntervals = await evaluate("window.collectFrames=false;window.frameIntervals");
  frameIntervals.sort((left, right) => left - right);
  assert.ok(peakNodes < 100, `滚动过程中挂载数量应受视口限制：${peakNodes}`);
  await evaluate(`(() => {
    window.savedSyntheticPositions = {};
    canvasTest.patch({
      nodes: [{...canvasTest.nodes.find(node => node.id === 'source'), state:'error', error:'拖拽回归测试'}],
      knownPlanspaceIds: ['interaction'], hiddenPlanspaceIds: [],
      focusedPlanspaceId: 'interaction', canMutateContextLayout: true,
      contextBundlesByNodeId: {source: {sources: [
        {scope:'project-root',kind:'context',path:'CONTEXT.md',chars:20,sha256:'hash',injection:'system'},
        {scope:'contextspace',kind:'planspace',path:'lane/CONTEXT.md',plug_id:'interaction',chars:20,sha256:'hash',injection:'system'}
      ]}},
      onNodePositionsChange: updates => Object.assign(window.savedSyntheticPositions, updates),
    });
  })()`);
  const syntheticIds = ['err:source', 'ctx:project-root::context::CONTEXT.md', 'ctx:contextspace::planspace::lane/CONTEXT.md'];
  const syntheticTransforms = {};
  for (const nodeId of syntheticIds) {
    await focus(nodeId);
    const point = center(await bounds(nodeId));
    const before = await evaluate(`document.querySelector('[data-id="${nodeId}"]').style.transform`);
    await mouse('mouseMoved', point);
    await mouse('mousePressed', point, { button: 'left', buttons: 1, clickCount: 1 });
    await mouse('mouseMoved', { x: point.x + 90, y: point.y + 65 }, { buttons: 1 });
    await mouse('mouseReleased', { x: point.x + 90, y: point.y + 65 }, { button: 'left', buttons: 0, clickCount: 1 });
    await eventually(`window.savedSyntheticPositions[${JSON.stringify(nodeId)}]`);
    await delay(150);
    syntheticTransforms[nodeId] = await evaluate(`document.querySelector('[data-id="${nodeId}"]').style.transform`);
    assert.notEqual(syntheticTransforms[nodeId], before, `${nodeId} 应能真实拖动`);
    const space = await evaluate(`window.savedSyntheticPositions[${JSON.stringify(nodeId)}].space`);
    assert.equal(space, nodeId.includes('project-root') ? 'canvas' : 'planspace:interaction');
  }
  await evaluate('canvasTest.patch({initialNodePositions:{...window.savedSyntheticPositions}})');
  await delay(100);
  await evaluate('canvasTest.remount()');
  for (const nodeId of syntheticIds) {
    await focus(nodeId);
    assert.equal(await evaluate(`document.querySelector('[data-id="${nodeId}"]').style.transform`), syntheticTransforms[nodeId], `${nodeId} 重新挂载应恢复位置`);
  }
  await evaluate('canvasTest.patch({canMutateContextLayout:false,canMutateNode:()=>false})');
  for (const nodeId of syntheticIds) {
    await focus(nodeId);
    assert.equal(await evaluate(`document.querySelector('[data-id="${nodeId}"]').classList.contains('draggable')`), false);
  }
  console.log('context 与错误卡片真实拖拽、坐标空间、重新挂载恢复及只读权限验证通过');
  console.log(`视口浏览器回归通过：${initial.total} 个图元素初始仅挂载 ${initial.rendered} 个节点、${initial.edges.length} 条边；提问／权限草稿保留及裁剪恢复、跨视口边、框选、多选、菜单／平移、跨视口长按连线、定位／恢复、泳道及模板折叠、适配和缩放通过`);
  console.log(`滚动采样：最多挂载 ${peakNodes} 个节点，帧间隔中位数 ${frameIntervals[Math.floor(frameIntervals.length / 2)]?.toFixed(1)} ms，P95 ${frameIntervals[Math.floor(frameIntervals.length * 0.95)]?.toFixed(1)} ms（仅报告，不设机器相关耗时阈值）`);
  assert.deepEqual(errors, []);
} finally {
  for (const request of pending.values()) clearTimeout(request.timer);
  if (browser && browser.exitCode === null && browser.signalCode === null) {
    const exited = once(browser, "exit");
    browser.kill("SIGTERM");
    await exited;
  }
  await rm(temporary, { recursive: true, force: true });
}
