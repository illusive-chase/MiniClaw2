import assert from "node:assert/strict";
import { loadConfigFromFile } from "vite";

const loaded = await loadConfigFromFile(
  { command: "serve", mode: "development" },
  new URL("../vite.config.ts", import.meta.url).pathname,
);
assert.ok(loaded);
assert.equal(
  loaded.config.server.proxy["/migrations"],
  process.env.MINICLAW_BACKEND_URL ?? "http://127.0.0.1:8000",
);
console.log("开发模式迁移状态代理校验通过");
