import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";

const browserBin = process.env.BROWSER_BIN;
assert.ok(browserBin, "请设置 BROWSER_BIN，指向 Chrome、Chromium 或 Edge 可执行文件");
const temporary = await mkdtemp(join(tmpdir(), "miniclaw2-sync-progress-browser-"));
try {
  const bundle = await build({
    entryPoints: [new URL("./sync-progress-browser.test.tsx", import.meta.url).pathname],
    bundle: true, format: "esm", write: false, logLevel: "warning",
  });
  const html = join(temporary, "test.html");
  await writeFile(html, `<!doctype html><html><head><meta charset="utf-8"></head><body><script type="module">${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script></body></html>`);
  const { stdout } = await promisify(execFile)(browserBin, [
    "--headless=new", "--no-first-run", "--no-default-browser-check",
    "--disable-background-networking", `--user-data-dir=${join(temporary, "profile")}`,
    "--dump-dom", "--virtual-time-budget=10000", pathToFileURL(html).href,
  ], { timeout: 30000, maxBuffer: 8 * 1024 * 1024 });
  assert.equal(stdout.match(/<body data-test-result="([^"]+)">/)?.[1], "passed",
    stdout.match(/<pre>([\s\S]*?)<\/pre>/)?.[1] ?? "同步进度浏览器回归未完成");
  console.log("同步进度浏览器回归通过：阶段显示、静默重试、防重叠、关闭与卸载清理、成功与失败收尾");
} finally {
  await rm(temporary, { recursive: true, force: true });
}
