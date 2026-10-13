import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { loadConfigFromFile } from "vite";

const backendUrl = process.env.MINICLAW_BACKEND_URL ?? "http://127.0.0.1:8000";
const loaded = await loadConfigFromFile(
  { command: "serve", mode: "development" },
  new URL("../vite.config.ts", import.meta.url).pathname,
);
assert.ok(loaded);
const proxy = loaded.config.server.proxy;
assert.equal(proxy["/auth"], backendUrl);
assert.equal(proxy["/migrations"], backendUrl);
assert.equal(proxy["/feishu"], backendUrl);

// Every top-level path the app fetches must be proxied; an unproxied one is
// answered by Vite's SPA fallback with index.html, which then fails JSON parsing.
function sourceFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(entry.name) ? [path] : [];
  });
}
const prefixes = new Set();
for (const file of sourceFiles(new URL("../src", import.meta.url).pathname)) {
  for (const match of readFileSync(file, "utf8").matchAll(/\bfetch\(\s*["'`](\/[A-Za-z0-9_-]+)/g)) {
    prefixes.add(match[1]);
  }
}
assert.ok(prefixes.has("/sessions"), "未扫描到前端请求路径");
for (const prefix of [...prefixes].sort()) {
  assert.equal(proxy[prefix], backendUrl, `开发代理缺少 ${prefix}`);
}
console.log("开发模式代理校验通过");
