import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";
import { chromium } from "playwright-core";

const browser = process.env.BROWSER_BIN;
assert.ok(browser, "请设置 BROWSER_BIN");
const temporary = await mkdtemp(join(tmpdir(), "miniclaw2-motion-ui-"));
let instance;
try {
  const bundle = await build({ entryPoints: [new URL("./motion-browser.test.tsx", import.meta.url).pathname], bundle: true, format: "esm", write: false, logLevel: "warning" });
  const css = await postcss([tailwindcss(new URL("../tailwind.config.ts", import.meta.url).pathname)]).process(await readFile(new URL("../src/index.css", import.meta.url), "utf8"), { from: undefined });
  const html = join(temporary, "test.html");
  await writeFile(html, `<!doctype html><html><head><meta charset="utf-8"><style>${css.css}</style></head><body><script type="module">${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script></body></html>`);
  instance = await chromium.launch({ executablePath: browser, headless: true });
  /* Both motion settings: the timing contract must hold either way. */
  for (const reducedMotion of ["no-preference", "reduce"]) {
    const page = await instance.newPage({ reducedMotion });
    await page.goto(pathToFileURL(html).href);
    await page.waitForFunction(() => document.body.dataset.testResult, undefined, { timeout: 10000 });
    const result = await page.locator("body").getAttribute("data-test-result");
    assert.equal(result, "passed", `${reducedMotion}: ${await page.locator("pre").allTextContents()}`);
    await page.close();
  }
  console.log("动效浏览器测试通过：模态退场时序、退场不拦截点击、中途重开、Esc 只关最上层、焦点归还、折叠展开与收起（含减少动态）");
} finally {
  await instance?.close();
  await rm(temporary, { recursive: true, force: true });
}
