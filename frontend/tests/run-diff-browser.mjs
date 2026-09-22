import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("../dist", import.meta.url));
const artifact = {
  kind: "miniclaw2.diff/v1",
  base: { tree: "a".repeat(40), at: "2026-09-22T10:00:00Z" },
  head: { tree: "b".repeat(40), at: "2026-09-22T10:05:00Z" },
  concurrent_node_ids: ["abcd1234efgh", "9876fedcba"],
  totals: { files: 3, additions: 18, deletions: 6 },
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
  ],
};

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
  assert.ok(await page.getByText("3 files", { exact: true }).count() >= 1);
  assert.equal(await page.locator("text=以下改动可能包含它们的产出").count(), 1);
  await page.screenshot({ path: "/tmp/miniclaw2-diff-review-desktop.png", fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "before" }).click();
  assert.equal(await page.locator("select").count(), 1);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await page.screenshot({ path: "/tmp/miniclaw2-diff-review-mobile.png", fullPage: true });
  console.log("diff viewer browser test passed");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
