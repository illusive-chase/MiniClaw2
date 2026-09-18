import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";
import { chromium } from "playwright-core";

const browser = process.env.BROWSER_BIN;
assert.ok(browser, "请设置 BROWSER_BIN");
const temporary = await mkdtemp(join(tmpdir(), "miniclaw2-remote-ui-"));
let instance;
try {
  const bundle = await build({ entryPoints: [new URL("./remote-project-browser.test.tsx", import.meta.url).pathname], bundle: true, format: "esm", write: false, logLevel: "warning" });
  const css = await postcss([tailwindcss(new URL("../tailwind.config.ts", import.meta.url).pathname)]).process(await readFile(new URL("../src/index.css", import.meta.url), "utf8"), { from: undefined });
  const html = join(temporary, "test.html");
  await writeFile(html, `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>${css.css}</style></head><body><script type="module">${bundle.outputFiles[0].text.replaceAll("</script", "<\\/script")}</script></body></html>`);
  instance = await chromium.launch({ executablePath: browser, headless: true });
  for (const [width, height] of [[1280, 900], [390, 844]]) {
    const size = `${width},${height}`;
    const directory = process.env.REMOTE_UI_SCREENSHOT_DIR ?? temporary;
    await mkdir(directory, { recursive: true });
    const page = await instance.newPage({ viewport: { width, height } });
    await page.goto(pathToFileURL(html).href);
    await page.waitForFunction(() => document.body.dataset.testResult, undefined, { timeout: 10000 });
    const result = await page.locator("body").getAttribute("data-test-result");
    assert.equal(result, "passed", await page.locator("pre").allTextContents());
    assert.equal(await page.evaluate(() => innerWidth), width);
    await page.screenshot({ path: join(directory, `remote-${size}.png`) });
    await page.close();
  }
  console.log("远端表单浏览器测试通过：克隆入口、默认关闭、沙箱警告、本机配置保存及桌面/移动端布局");
} finally {
  await instance?.close();
  await rm(temporary, { recursive: true, force: true });
}
