import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const browserBin = process.env.BROWSER_BIN;
assert.ok(browserBin, "请设置 BROWSER_BIN，指向 Chrome、Chromium 或 Edge 可执行文件");
const fixtures = JSON.parse(execFileSync(process.env.PYTHON || "python", [
  fileURLToPath(new URL("../../backend/tests/test_svg_security.py", import.meta.url)),
], { encoding: "utf8" }));
const bundle = await build({
  stdin: {
    contents: `
      import { createRoot } from 'react-dom/client';
      import { SvgArtifactPreview } from './panel/SvgArtifactPreview';
      const name = new URLSearchParams(location.search).get('name');
      createRoot(document.getElementById('root')).render(
        <SvgArtifactPreview name={name} rawUrl={'/raw/' + name} />
      );`,
    resolveDir: fileURLToPath(new URL("../src", import.meta.url)),
    loader: "tsx",
  },
  jsx: "automatic",
  bundle: true,
  write: false,
  logLevel: "warning",
});
const unexpected = [];
const server = createServer((request, response) => {
  const path = new URL(request.url, "http://localhost").pathname;
  if (path === "/") {
    response.setHeader("Content-Type", "text/html; charset=utf-8");
    response.end('<!doctype html><div id="root"></div><script src="/bundle.js"></script>');
  } else if (path === "/bundle.js") {
    response.setHeader("Content-Type", "text/javascript");
    response.end(bundle.outputFiles[0].contents);
  } else if (path.startsWith("/raw/") && fixtures[path.slice(5)]) {
    const fixture = fixtures[path.slice(5)];
    response.writeHead(200, fixture.headers);
    response.end(fixture.body);
  } else if (path === "/favicon.ico") {
    response.writeHead(204).end();
  } else {
    unexpected.push(path);
    response.writeHead(404).end();
  }
});
const temporary = await mkdtemp(join(tmpdir(), "miniclaw2-svg-browser-"));
let browser;
let socket;
try {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const origin = `http://127.0.0.1:${server.address().port}`;
  browser = spawn(browserBin, [
    "--headless=new", "--no-first-run", "--no-default-browser-check",
    "--disable-background-networking", "--remote-debugging-port=0",
    `--user-data-dir=${temporary}`, "about:blank",
  ], { stdio: "ignore" });
  let launchError;
  browser.on("error", (error) => { launchError = error; });
  async function eventually(check) {
    for (let attempt = 0; attempt < 150; attempt += 1) {
      if (launchError) throw launchError;
      if (browser.exitCode !== null) throw new Error("浏览器在测试完成前退出");
      const value = await check();
      if (value) return value;
      await delay(100);
    }
    throw new Error("等待浏览器测试条件超时");
  }
  const endpoint = await eventually(async () => {
    try {
      const [port, path] = (await readFile(join(temporary, "DevToolsActivePort"), "utf8")).trim().split("\n");
      return `ws://127.0.0.1:${port}${path}`;
    } catch { return null; }
  });
  socket = new WebSocket(endpoint);
  await once(socket, "open");
  const pending = new Map();
  const events = [];
  let nextId = 0;
  socket.addEventListener("message", (message) => {
    const payload = JSON.parse(message.data);
    if (!payload.id) { events.push(payload); return; }
    const request = pending.get(payload.id);
    if (!request) return;
    pending.delete(payload.id);
    clearTimeout(request.timer);
    if (payload.error) request.reject(new Error(JSON.stringify(payload.error)));
    else request.resolve(payload.result);
  });
  function command(method, params = {}, sessionId) {
    return new Promise((resolve, reject) => {
      const id = ++nextId;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`浏览器命令超时：${method}`));
      }, 10000);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ id, method, params, sessionId }));
    });
  }
  const { browserContextId } = await command("Target.createBrowserContext");
  const { targetId } = await command("Target.createTarget", { url: "about:blank", browserContextId });
  const { sessionId } = await command("Target.attachToTarget", { targetId, flatten: true });
  const page = (method, params) => command(method, params, sessionId);
  const evaluate = async (expression) => {
    const result = await page("Runtime.evaluate", { expression, returnByValue: true });
    assert.equal(result.exceptionDetails, undefined);
    return result.result.value;
  };
  await command("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: temporary, eventsEnabled: true, browserContextId });
  await page("Page.enable");
  await page("Network.enable");
  for (const name of Object.keys(fixtures)) {
    const url = `${origin}/?name=${name}`;
    await page("Page.navigate", { url });
    await eventually(() => evaluate(`document.querySelector('img')?.getAttribute('src') === ${JSON.stringify(`/raw/${name}`)} && document.querySelector('img').complete`));
    assert.ok(await evaluate("document.querySelector('img').naturalWidth > 0"), name);
    if (name === "safe.svg") {
      const pixels = await evaluate(`(() => {
        const canvas = document.createElement('canvas'); canvas.width = 160; canvas.height = 80;
        const context = canvas.getContext('2d'); context.drawImage(document.querySelector('img'), 0, 0);
        return [[10, 10], [130, 10]].map(([left, top]) => [...context.getImageData(left, top, 1, 1).data]);
      })()`);
      assert.deepEqual(pixels, [[0, 128, 0, 255], [255, 0, 0, 255]]);
    }
    const center = await evaluate(`(() => {
      const rect = document.querySelector('img').getBoundingClientRect();
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
    })()`);
    await page("Input.dispatchMouseEvent", { type: "mousePressed", button: "left", clickCount: 1, ...center });
    await page("Input.dispatchMouseEvent", { type: "mouseReleased", button: "left", clickCount: 1, ...center });
    await delay(150);
    assert.equal(await evaluate("location.href"), url);
    assert.equal(await evaluate("Boolean(window.__svgAttack)"), false);
    assert.equal(await evaluate("document.querySelectorAll('iframe, object, embed').length"), 0);
    assert.equal(events.filter((event) => event.method === "Browser.downloadWillBegin").length, 0);
  }
  assert.deepEqual(unexpected, []);
  const requests = events.filter((event) => event.method === "Network.requestWillBeSent");
  assert.ok(requests.every((event) => event.params.request.url.startsWith(origin)));
  const targets = await command("Target.getTargets");
  assert.deepEqual(
    targets.targetInfos.filter((entry) => entry.type === "page" && entry.browserContextId === browserContextId).map((entry) => entry.targetId),
    [targetId],
  );
  await page("Page.navigate", { url: `${origin}/raw/active.svg` });
  await eventually(() => events.find((event) => event.method === "Browser.downloadProgress" && event.params.state === "completed"));
  const download = events.find((event) => event.method === "Browser.downloadWillBegin");
  assert.equal(download.params.suggestedFilename, "active.svg");
  assert.equal(await readFile(join(temporary, "active.svg"), "utf8"), fixtures["active.svg"].body);
  assert.equal(await evaluate("Boolean(window.__svgAttack)"), false);
  assert.deepEqual(unexpected, []);
  console.log("SVG 浏览器验证通过：内联样式和 data URI 正常，脚本、外链、导航、弹窗及隐式下载均未执行；原始地址仅下载");
} finally {
  socket?.close();
  if (browser && browser.exitCode === null && browser.pid) {
    const exited = once(browser, "exit");
    browser.kill("SIGTERM");
    await exited;
  }
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
  await rm(temporary, { recursive: true, force: true });
}
