import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("../dist", import.meta.url));
const longBefore = `const value = "${"a".repeat(400)}";`;
const longAfter = `const value = "${"b ".repeat(200)}";`;
const artifact = {
  kind: "miniclaw2.diff/v1",
  base: { tree: "a".repeat(40), at: "2026-09-22T10:00:00Z" },
  head: { tree: "b".repeat(40), at: "2026-09-22T10:05:00Z" },
  concurrent_node_ids: ["abcd1234efgh", "9876fedcba"],
  totals: { files: 6, additions: 20, deletions: 7 },
  truncated: true,
  files: [
    {
      path: "backend/miniclaw2/runner.py",
      status: "modified",
      additions: 12,
      deletions: 6,
      binary: false,
      inlined: true,
      before: "def run():\n    old = True\n    return old\n",
      after: "def run():\n    snapshot = capture()\n    return snapshot\n",
    },
    {
      path: "frontend/src/components/DiffViewer.tsx",
      status: "added",
      additions: 6,
      deletions: 0,
      binary: false,
      inlined: true,
      before: null,
      after: "export function DiffViewer() {\n  return <main />;\n}\n",
    },
    {
      path: "frontend/package-lock.json",
      status: "modified",
      additions: 0,
      deletions: 0,
      binary: false,
      inlined: false,
      omitted_reason: "exceeds inline budget",
    },
    {
      path: "scripts/check.sh",
      status: "modified",
      old_mode: "100644",
      new_mode: "100755",
      additions: 0,
      deletions: 0,
      binary: false,
      inlined: true,
      before: "#!/bin/sh\n",
      after: "#!/bin/sh\n",
    },
    {
      path: "config/value.txt",
      status: "modified",
      old_mode: "100644",
      new_mode: "100644",
      additions: 0,
      deletions: 0,
      binary: false,
      inlined: true,
      before: "VALUE=1",
      after: "VALUE=1\n",
    },
    {
      path: "src/long.ts",
      status: "modified",
      additions: 2,
      deletions: 1,
      binary: false,
      inlined: true,
      before: `head\n${longBefore}\ntail\n`,
      after: `head\n${longAfter}\nextra\ntail\n`,
    },
  ],
};

/* Geometry of the diff body: every rendered row's sides, and the scroll
 * containers involved. Rows are matched across sides by `data-row`. */
function diffGeometry() {
  const scroller = document.querySelector("[data-row]")?.closest(".overflow-y-auto");
  const rows = Array.from(document.querySelectorAll("[data-row]"))
    .filter((element) => element.getBoundingClientRect().height > 0)
    .map((element) => {
      const rect = element.getBoundingClientRect();
      return { key: element.dataset.row, top: rect.top, height: rect.height, bottom: rect.bottom };
    });
  const pres = Array.from(document.querySelectorAll("section pre"))
    .filter((element) => element.getBoundingClientRect().height > 0)
    .map((element) => ({ scroll: element.scrollWidth, client: element.clientWidth }));
  const panes = Array.from(document.querySelectorAll(".diff-pane-scroll"))
    .map((element) => ({ scroll: element.scrollWidth, client: element.clientWidth, left: element.scrollLeft }));
  return {
    rows,
    pres,
    panes,
    scrollerOverflowX: scroller ? scroller.scrollWidth - scroller.clientWidth : null,
  };
}

const mime = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
};
const server = createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", "http://localhost");
  if (url.pathname === "/auth/state") {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({ required: false, authenticated: true, locked: false }));
    return;
  }
  if (url.pathname.includes("/artifacts/run-diff.json")) {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify(artifact));
    return;
  }
  const path = url.pathname === "/" ? join(root, "index.html") : join(root, url.pathname);
  try {
    response.setHeader("Content-Type", mime[extname(path)] ?? "application/octet-stream");
    response.end(await readFile(path));
  } catch {
    response.writeHead(404).end();
  }
});

let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  browser = await chromium.launch({
    executablePath: process.env.BROWSER_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
  });
  const page = await browser.newPage({ viewport: { width: 1366, height: 850 } });
  await page.goto(`http://127.0.0.1:${address.port}/#/diff?src=diff&session=p1&node=n1&name=run-diff.json`);
  await page.locator("section").getByText("backend/miniclaw2/runner.py", { exact: true }).waitFor();
  assert.ok(await page.getByText("6 files", { exact: true }).count() >= 1);
  assert.equal(await page.locator("text=以下改动可能包含它们的产出").count(), 1);
  await page.getByRole("button", { name: /scripts\/check\.sh/ }).click();
  assert.equal(await page.getByText("100644 -> 100755", { exact: true }).count(), 1);
  await page.getByRole("button", { name: /config\/value\.txt/ }).click();
  assert.equal(await page.getByText("\\ 文件末尾没有换行符", { exact: true }).count(), 1);
  await page.screenshot({ path: "/tmp/miniclaw2-diff-review-desktop.png", fullPage: true });

  /* Wrapped (the default): long lines break inside their column and grow the
   * row, and both sides of a row share one height so line numbers stay level. */
  await page.getByRole("button", { name: /src\/long\.ts/ }).click();
  const wrapToggle = page.getByRole("button", { name: "自动换行" });
  assert.equal(await wrapToggle.getAttribute("aria-pressed"), "true");
  let geometry = await page.evaluate(diffGeometry);
  assert.equal(geometry.panes.length, 0);
  assert.equal(geometry.scrollerOverflowX, 0);
  for (const pre of geometry.pres) assert.ok(pre.scroll <= pre.client + 1, "wrapped text must not overflow its cell");
  assert.ok(geometry.rows.some((row) => row.height > 40), "a 400-char line must wrap onto several lines");
  for (let index = 1; index < geometry.rows.length; index += 1) {
    assert.ok(geometry.rows[index].top >= geometry.rows[index - 1].bottom - 0.5, "wrapped rows must not overlap");
  }
  const wrappedCells = await page.evaluate(() =>
    Array.from(document.querySelectorAll("[data-row]")).map((row) =>
      Array.from(row.children).map((cell) => cell.getBoundingClientRect().height)),
  );
  for (const heights of wrappedCells) {
    if (heights.length === 2) assert.equal(heights[0], heights[1], "both sides of a wrapped row share its height");
  }
  await page.screenshot({ path: "/tmp/miniclaw2-diff-wrap-desktop.png" });

  /* Unwrapped: every line is one row, each side scrolls sideways on its own,
   * and the two sides still line up row for row. */
  await wrapToggle.click();
  assert.equal(await wrapToggle.getAttribute("aria-pressed"), "false");
  assert.equal(await page.evaluate(() => window.localStorage.getItem("miniclaw.diffWrap")), "nowrap");
  geometry = await page.evaluate(diffGeometry);
  assert.equal(geometry.panes.length, 2);
  assert.equal(geometry.scrollerOverflowX, 0);
  for (const pane of geometry.panes) assert.ok(pane.scroll > pane.client, "long lines overflow their pane");
  const byKey = new Map();
  for (const row of geometry.rows) {
    if (!byKey.has(row.key)) byKey.set(row.key, []);
    byKey.get(row.key).push(row);
  }
  for (const [key, sides] of byKey) {
    assert.equal(sides.length, 2, `row ${key} renders on both sides`);
    assert.equal(sides[0].top, sides[1].top, `row ${key} is level across sides`);
    assert.equal(sides[0].height, 20);
  }
  const proxies = page.locator(".sticky.bottom-0 .overflow-x-auto");
  assert.equal(await proxies.count(), 2);
  const scrolled = await page.evaluate(async () => {
    const [before, after] = document.querySelectorAll(".diff-pane-scroll");
    const [beforeProxy, afterProxy] = document.querySelectorAll(".sticky.bottom-0 .overflow-x-auto");
    const frame = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    beforeProxy.scrollLeft = 120;
    await frame();
    const fromProxy = { pane: before.scrollLeft, other: after.scrollLeft, otherProxy: afterProxy.scrollLeft };
    after.scrollLeft = 60;
    await frame();
    const gutter = before.querySelector("[data-row] > span").getBoundingClientRect().left;
    return {
      fromProxy,
      fromPane: afterProxy.scrollLeft,
      beforeStill: before.scrollLeft,
      gutterPinned: Math.abs(gutter - before.getBoundingClientRect().left) < 1,
      rangesMatch: beforeProxy.scrollWidth - beforeProxy.clientWidth === before.scrollWidth - before.clientWidth,
    };
  });
  assert.deepEqual(scrolled.fromProxy, { pane: 120, other: 0, otherProxy: 0 });
  assert.equal(scrolled.fromPane, 60);
  assert.equal(scrolled.beforeStill, 120);
  assert.equal(scrolled.gutterPinned, true, "line numbers stay in view while scrolled sideways");
  assert.equal(scrolled.rangesMatch, true);
  await page.screenshot({ path: "/tmp/miniclaw2-diff-nowrap-desktop.png" });

  /* Alt+Z toggles like VS Code, and the choice survives a reload. */
  await page.keyboard.press("Alt+KeyZ");
  assert.equal(await wrapToggle.getAttribute("aria-pressed"), "true");
  await page.keyboard.press("Alt+KeyZ");
  assert.equal(await wrapToggle.getAttribute("aria-pressed"), "false");
  await page.reload();
  await page.getByRole("button", { name: /src\/long\.ts/ }).click();
  assert.equal(await page.getByRole("button", { name: "自动换行" }).getAttribute("aria-pressed"), "false");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "before" }).click();
  assert.equal(await page.locator("select").count(), 1);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  geometry = await page.evaluate(diffGeometry);
  assert.equal(geometry.panes.filter((pane) => pane.client > 0).length, 1);
  assert.equal(await page.locator(".sticky.bottom-0 .overflow-x-auto").count(), 1);
  await page.getByRole("button", { name: "自动换行" }).click();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  geometry = await page.evaluate(diffGeometry);
  for (const pre of geometry.pres) assert.ok(pre.scroll <= pre.client + 1, "wrapped text must not overflow on mobile");
  await page.screenshot({ path: "/tmp/miniclaw2-diff-review-mobile.png", fullPage: true });
  console.log("diff viewer browser test passed");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
