import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

/* The code-reading page against a mock backend whose analysis payloads come
 * from the real analyser (tests/code-reader-fixtures.py). */
const root = fileURLToPath(new URL("../dist", import.meta.url));
const fixtures = JSON.parse(
  execFileSync(process.env.PYTHON || "python3", [fileURLToPath(new URL("./code-reader-fixtures.py", import.meta.url))], {
    encoding: "utf-8",
    maxBuffer: 64 * 1024 * 1024,
  }),
);
const tree = {
  root_name: "demo",
  is_git: true,
  truncated: false,
  files: [
    ...Object.keys(fixtures).map((path) => ({ path, size: fixtures[path].text.length })),
    { path: "README.md", size: 12 },
  ],
};
const readme = "# demo\n\ntext\n";

/* Analysis is "not ready" for the first request of each file, like a real
 * first visit, then ready once the page re-asks after a POST. */
const analysisRequests = new Map();
let indexPosts = 0;
let indexPolls = 0;
/* A later run that re-resolves main.py because pkg/core.py changed: its own
 * sha256 stays, its symbols (here Engine's doc) do not. */
let rerun = null;
let revision = 0;
let updatedAt = 1;
function revised(path) {
  const analysis = fixtures[path].analysis;
  if (revision === 0 || path !== "main.py") return analysis;
  return {
    ...analysis,
    symbols: analysis.symbols.map((symbol) => symbol.name === "Engine" ? { ...symbol, doc: "Runs things, revised." } : symbol),
  };
}

const mime = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
};

function json(response, status, body, headers = {}) {
  response.writeHead(status, { "Content-Type": "application/json", ...headers });
  response.end(JSON.stringify(body));
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", "http://localhost");
  const path = url.searchParams.get("path") ?? "";
  if (url.pathname === "/auth/state") return json(response, 200, { required: false, authenticated: true, locked: false });
  if (url.pathname === "/sessions/p1") {
    return json(response, 200, { id: "p1", name: "Demo", root_path: "/work/demo", capabilities: { code_reading: true } });
  }
  if (url.pathname === "/sessions/p1/code/tree") return json(response, 200, tree);
  if (url.pathname === "/sessions/p1/code/file") {
    if (path === "README.md") {
      return json(response, 200, { path, text: readme, sha256: "r", size: readme.length, language: "other", binary: false, too_large: false });
    }
    const fixture = fixtures[path];
    if (!fixture) return json(response, 404, { detail: "文件不存在" });
    return json(response, 200, {
      path, text: fixture.text, sha256: fixture.sha256, size: fixture.text.length,
      language: "python", binary: false, too_large: false,
    });
  }
  if (url.pathname === "/sessions/p1/code/index") {
    if (request.method === "POST") indexPosts += 1;
    else indexPolls += 1;
    let running = indexPosts <= 1 && indexPolls < 2;
    if (rerun) {
      if (request.method === "POST") rerun.started = true;
      else if (rerun.started) rerun.polls += 1;
      running = rerun.polls < 2;
      if (!running) {
        rerun = null;
        revision = 1;
        updatedAt = 2;
      }
    }
    return json(response, 200, {
      state: running ? "running" : "ready", phase: running ? "resolve" : null,
      done: running ? 1 : 4, total: 4, failed: 0, updated_at: updatedAt, error: null,
    });
  }
  if (url.pathname === "/sessions/p1/code/analysis") {
    const fixture = fixtures[path];
    if (!fixture) return json(response, 404, { detail: "none" });
    const seen = analysisRequests.get(path) ?? 0;
    analysisRequests.set(path, seen + 1);
    if (seen === 0) return json(response, 202, { pending: true });
    return json(response, 200, revised(path), { ETag: `"${path}-${revision}"`, "Cache-Control": "no-cache" });
  }
  const file = url.pathname === "/" ? join(root, "index.html") : join(root, url.pathname);
  try {
    response.setHeader("Content-Type", mime[extname(file)] ?? "application/octet-stream");
    response.end(await readFile(file));
  } catch {
    response.writeHead(404).end();
  }
});

const META = process.platform === "darwin" ? "Meta" : "Control";

let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const base = `http://127.0.0.1:${address.port}`;
  browser = await chromium.launch({
    executablePath: process.env.BROWSER_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
  });
  const context = await browser.newContext({ viewport: { width: 1366, height: 850 } });
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: base });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.goto(`${base}/#/code?session=p1`);

  /* 1. The tree appears; single clicks open one preview tab that is replaced. */
  const treeItem = (name) => page.locator('[role="treeitem"]').filter({ hasText: new RegExp(`^${name}$`) });
  await treeItem("main.py").waitFor();
  assert.equal(await page.title(), "Demo · 代码阅读 · MiniClaw2");
  await treeItem("README.md").click();
  await page.locator('[role="tab"]').filter({ hasText: "README.md" }).waitFor();
  assert.equal(await page.locator('[role="tab"] span.italic').count(), 1);
  await treeItem("main.py").click();
  await page.locator('[role="tab"]').filter({ hasText: "main.py" }).waitFor();
  assert.equal(await page.locator('[role="tab"]').count(), 1, "a second preview replaces the first");

  /* 2. 202 then 200: the banner goes away and semantic classes appear. */
  await page.locator(".code-reader .tok-class").first().waitFor({ timeout: 10000 });
  assert.equal(await page.getByText("正在分析此文件").count(), 0);
  assert.ok(indexPosts >= 1, "the page asked for an index run");

  /* 3. Hover after 350 ms: signature and doc, no source excerpt. */
  const engineCall = page.locator('.code-reader .cl[data-ln="5"] [data-r]').filter({ hasText: /^Engine$/ });
  await engineCall.hover();
  const card = page.locator(".code-hover");
  await card.waitFor({ timeout: 2000 });
  const cardText = await card.innerText();
  assert.match(cardText, /\(class\) Engine/);
  assert.match(cardText, /Runs things\./);
  assert.doesNotMatch(cardText, /pkg\/core\.py|def start/);
  await page.mouse.move(5, 5);
  await card.waitFor({ state: "detached", timeout: 2000 });

  /* 3b. A finished run refreshes an already-ready tab without reopening it. */
  const mainRequests = analysisRequests.get("main.py");
  rerun = { started: false, polls: 0 };
  await page.getByRole("button", { name: "刷新文件树" }).click();
  const deadline = Date.now() + 10000;
  while (revision === 0 || analysisRequests.get("main.py") === mainRequests) {
    assert.ok(Date.now() < deadline, "the finished run re-asked for main.py");
    await page.waitForTimeout(100);
  }
  await page.waitForTimeout(200);
  await engineCall.hover();
  await card.waitFor({ timeout: 2000 });
  assert.match(await card.innerText(), /Runs things, revised\./);
  await page.mouse.move(5, 5);
  await card.waitFor({ state: "detached", timeout: 2000 });

  /* 4. Modifier shows the link; modifier-click jumps; Back restores. */
  const scroller = page.locator(".code-reader");
  await scroller.evaluate((node) => { node.scrollTop = 0; });
  const startCall = page.locator('.code-reader .cl[data-ln="6"] [data-r]').filter({ hasText: /^start$/ });
  await startCall.hover();
  await page.keyboard.down(META);
  await page.waitForFunction(() => document.querySelector(".code-reader [data-r].link")?.textContent === "start");
  await startCall.click({ modifiers: [META] });
  await page.keyboard.up(META);
  await page.locator('[role="tab"][aria-selected="true"]').filter({ hasText: "core.py" }).waitFor();
  await page.locator('.code-reader .cl[data-ln="4"].target-line').waitFor();
  await page.locator(".code-reader .flash").waitFor();
  assert.equal(await page.locator('[role="tab"]').count(), 1, "the preview origin was replaced by the target");
  await page.goBack();
  await page.locator('[role="tab"][aria-selected="true"]').filter({ hasText: "main.py" }).waitFor();
  assert.equal(await scroller.evaluate((node) => node.scrollTop), 0);

  /* 5. An already-open target is reused. */
  await page.locator('[role="tab"]').filter({ hasText: "main.py" }).dblclick();
  await treeItem("core.py").dblclick();
  await page.locator('[role="tab"]').filter({ hasText: "core.py" }).waitFor();
  await page.locator('[role="tab"]').filter({ hasText: "main.py" }).click();
  const before = await page.locator('[role="tab"]').count();
  const engineImport = page.locator('.code-reader .cl[data-ln="1"] [data-r]').filter({ hasText: /^Engine$/ });
  await engineImport.click({ modifiers: [META] });
  await page.locator('[role="tab"][aria-selected="true"]').filter({ hasText: "core.py" }).waitFor();
  assert.equal(await page.locator('[role="tab"]').count(), before, "jumping to an open file reuses its tab");

  /* 6. Find-in-page reaches off-screen text. */
  await treeItem("big.py").click();
  await page.locator('.code-reader .cl[data-ln="3200"]').waitFor({ state: "attached" });
  const found = await page.evaluate(() => {
    window.getSelection()?.removeAllRanges();
    return window.find("NEEDLE_AT_THE_END");
  });
  assert.ok(found, "window.find reaches line 3200");

  /* 7. Copy preserves empty lines and tabs. */
  await page.locator('[role="tab"]').filter({ hasText: "main.py" }).click();
  await page.locator('.code-reader .cl[data-ln="11"]').waitFor();
  await page.evaluate(() => {
    const start = document.querySelector('.code-reader .cl[data-ln="7"]');
    const end = document.querySelector('.code-reader .cl[data-ln="11"]');
    const range = document.createRange();
    range.setStart(start, 0);
    range.setEnd(end, end.childNodes.length);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  });
  await scroller.focus();
  await page.keyboard.press(`${META}+c`);
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  const expected = fixtures["main.py"].text.split("\n").slice(6, 11).join("\n");
  assert.equal(copied, expected);

  /* 8. Reload restores tabs and the scroll position. */
  await page.locator('[role="tab"]').filter({ hasText: "big.py" }).click();
  await page.locator('.code-reader .cl[data-ln="3200"]').waitFor({ state: "attached" });
  await scroller.evaluate((node) => { node.scrollTop = 20 * 19 * 50; });
  await page.waitForTimeout(2300);
  const tabsBefore = await page.locator('[role="tab"]').allInnerTexts();
  const topBefore = await scroller.evaluate((node) => node.scrollTop);
  await page.reload();
  await page.locator('[role="tab"][aria-selected="true"]').filter({ hasText: "big.py" }).waitFor();
  await page.waitForFunction((top) => {
    const node = document.querySelector(".code-reader");
    return node && Math.abs(node.scrollTop - top) <= 20;
  }, topBefore, { timeout: 5000 });
  assert.deepEqual(await page.locator('[role="tab"]').allInnerTexts(), tabsBefore);

  /* 9. Dark mode. */
  await page.locator('[role="tab"]').filter({ hasText: "main.py" }).click();
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  await page.screenshot({ path: "/tmp/miniclaw2-code-reader-dark.png" });
  const darkKeyword = await page.locator(".code-reader .tok-keyword").first().evaluate((node) => getComputedStyle(node).color);
  assert.equal(darkKeyword, "rgb(196, 155, 216)");
  await page.evaluate(() => document.documentElement.classList.remove("dark"));
  await page.screenshot({ path: "/tmp/miniclaw2-code-reader-light.png" });

  /* 10. Narrow: the tree becomes a drawer. */
  await page.setViewportSize({ width: 600, height: 800 });
  await page.getByRole("button", { name: "切换文件树" }).waitFor();
  assert.equal(await page.locator('[role="tree"]').count(), 0);
  await page.getByRole("button", { name: "切换文件树" }).click();
  await page.locator('[role="tree"]').waitFor();
  await page.screenshot({ path: "/tmp/miniclaw2-code-reader-narrow.png" });

  assert.deepEqual(errors, []);
  console.log("code-reader browser ok");
} finally {
  await browser?.close();
  server.close();
}
