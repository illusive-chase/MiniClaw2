import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const browserBin = process.env.BROWSER_BIN;
assert.ok(browserBin, "请设置 BROWSER_BIN，指向 Chrome、Chromium 或 Edge 可执行文件");
const temporary = await mkdtemp(join(tmpdir(), "miniclaw2-node-detail-browser-"));
try {
  const bundle = await build({
    entryPoints: [new URL("./node-detail-browser.test.tsx", import.meta.url).pathname],
    bundle: true, format: "esm", write: false, logLevel: "warning",
  });
  const sourceCss = await readFile(new URL("../src/index.css", import.meta.url), "utf8");
  const css = await postcss([tailwindcss(new URL("../tailwind.config.ts", import.meta.url).pathname)]).process(sourceCss, { from: undefined });
  const html = join(temporary, "test.html");
  await writeFile(html, `<!doctype html><html><head><meta charset="utf-8"><style>${css.css}\n#test-root{width:420px;height:800px}</style></head><body><script type="module">${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script></body></html>`);
  const { stdout } = await promisify(execFile)(browserBin, [
    "--headless=new", "--no-first-run", "--no-default-browser-check",
    "--disable-background-networking", `--user-data-dir=${join(temporary, "profile")}`,
    "--dump-dom", "--virtual-time-budget=10000", pathToFileURL(html).href,
  ], { timeout: 30000, maxBuffer: 8 * 1024 * 1024 });
  const result = stdout.match(/<body data-test-result="([^"]+)">/)?.[1];
  assert.equal(result, "passed", stdout.match(/<pre>([\s\S]*?)<\/pre>/)?.[1] ?? "节点详情浏览器回归未完成");
  console.log("节点详情浏览器回归通过：快速切换、版本失效、缓存隔离、错误重试、草稿恢复后提升及自动保存焦点与布局保持");
} finally {
  await rm(temporary, { recursive: true, force: true });
}
