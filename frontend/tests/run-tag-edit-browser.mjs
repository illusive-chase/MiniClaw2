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
const temporary = await mkdtemp(join(tmpdir(), "miniclaw2-tag-edit-browser-"));
try {
  const bundle = await build({
    entryPoints: [new URL("./tag-edit-browser.test.tsx", import.meta.url).pathname],
    bundle: true,
    format: "esm",
    write: false,
    logLevel: "warning",
  });
  const html = join(temporary, "test.html");
  await writeFile(html, `<!doctype html><html><head><meta charset="utf-8"></head><body><script type="module">${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script></body></html>`);
  const { stdout } = await promisify(execFile)(browserBin, [
    "--headless=new", "--no-first-run", "--no-default-browser-check",
    "--disable-background-networking", `--user-data-dir=${join(temporary, "profile")}`,
    "--dump-dom", "--virtual-time-budget=10000", pathToFileURL(html).href,
  ], { timeout: 30000, maxBuffer: 8 * 1024 * 1024 });
  assert.match(stdout.replace(/<script[\s\S]*?<\/script>/g, ""), /<body data-test-result="passed">/);
  console.log("标签浏览器回归测试通过：外点提交、失败保留、Escape 取消、失焦／回车去重及并发会话更新");
} finally {
  await rm(temporary, { recursive: true, force: true });
}
