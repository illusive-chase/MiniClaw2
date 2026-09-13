import assert from "node:assert/strict";

import { followMarkdownLink } from "../src/markdownLink";
import { parseMarkdownRoute } from "../src/markdownRoute";
import type { MarkdownLinkBase, MarkdownLinkVerdict } from "../src/types";

const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;

function setup({ blocked = false, throws = false } = {}) {
  const requests: { url: string; body: unknown }[] = [];
  const opened: { url: string; target: string | undefined }[] = [];
  const reader = { opener: {} as object | null };
  let resolveResponse!: (response: Response) => void;
  const response = new Promise<Response>((resolve) => {
    resolveResponse = resolve;
  });
  globalThis.window = {
    location: {
      pathname: "/app/",
      href: "http://localhost/app/#/canvas",
    },
    open: (url: string, target?: string) => {
      opened.push({ url, target });
      if (throws) throw new Error("浏览器拒绝打开窗口");
      return blocked ? null : reader;
    },
  } as unknown as Window & typeof globalThis;
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    requests.push({ url, body: JSON.parse(String(init?.body)) });
    if (url.endsWith("/files/resolve")) return response;
    assert.ok(url.endsWith("/files/reveal"));
    return Response.json({ path: "/project/backend/app.py" });
  };
  return {
    requests,
    opened,
    reader,
    resolveResponse,
    settle: (verdict: MarkdownLinkVerdict) => resolveResponse(Response.json(verdict)),
  };
}

try {
  const revealVerdict: MarkdownLinkVerdict = {
    verdict: "reveal",
    path: "/project/backend/app.py",
    relative_path: null,
    reason: null,
  };
  const markdownVerdict: MarkdownLinkVerdict = {
    verdict: "markdown",
    path: "/project/docs/阅读 #1.md",
    relative_path: "docs/阅读 #1.md",
    reason: null,
  };
  const bases: MarkdownLinkBase[] = [
    { kind: "project-root" },
    { kind: "project-file", path: "docs/source.md" },
    { kind: "artifact", path: ".miniclaw2/outputs/node/report.md" },
  ];
  for (const base of bases) {
    for (const href of ["backend/app.py:12:4", "app.py%3A12", "file:///project/app.py", "backend/", "../outside.md"]) {
      const context = setup();
      const pending = followMarkdownLink("session/1", href, base);
      assert.equal(context.opened.length, 0);
      assert.deepEqual(context.requests, [{
        url: "/sessions/session%2F1/files/resolve",
        body: { href, base },
      }]);
      context.settle(revealVerdict);
      const note = await pending;
      assert.equal(context.opened.length, 0);
      assert.deepEqual(context.requests[1], {
        url: "/sessions/session%2F1/files/reveal",
        body: { path: revealVerdict.path },
      });
      assert.equal(note?.kind, "info");
      assert.match(note?.text ?? "", /已在文件管理器中显示/);
      assert.equal(note?.href, undefined);
    }
  }

  for (const mode of [{}, { blocked: true }, { throws: true }]) {
    const context = setup(mode);
    const pending = followMarkdownLink("session/1", "docs/阅读%20%231.md:12");
    assert.equal(context.opened.length, 0);
    context.settle(markdownVerdict);
    const note = await pending;
    assert.equal(context.requests.length, 1);
    assert.equal(context.opened.length, 1);
    assert.equal(context.opened[0].target, "_blank");
    const url = new URL(context.opened[0].url);
    assert.equal(url.origin, "http://localhost");
    assert.equal(url.pathname, "/app/");
    assert.deepEqual(parseMarkdownRoute(url.hash), {
      src: "project-file",
      sessionId: "session/1",
      path: markdownVerdict.relative_path,
    });
    if (mode.blocked || mode.throws) {
      assert.equal(note?.kind, "info");
      assert.equal(note?.href, url.href);
    } else {
      assert.equal(note, null);
      assert.equal(context.reader.opener, null);
    }
  }

  {
    const context = setup();
    const pending = followMarkdownLink("session", "missing.py");
    assert.equal(context.opened.length, 0);
    context.settle({ verdict: "missing", path: null, relative_path: null, reason: "路径不存在" });
    assert.deepEqual(await pending, { kind: "error", text: "路径不存在" });
    assert.equal(context.opened.length, 0);
    assert.equal(context.requests.length, 1);
  }

  {
    const context = setup();
    const pending = followMarkdownLink("session", "app.py");
    context.resolveResponse(Response.json({ detail: "解析失败" }, { status: 400 }));
    const note = await pending;
    assert.equal(note?.kind, "error");
    assert.match(note?.text ?? "", /解析失败/);
    assert.equal(context.opened.length, 0);
  }

  {
    const context = setup();
    globalThis.fetch = async () => { throw new Error("连接失败"); };
    assert.deepEqual(await followMarkdownLink("session", "app.py"), {
      kind: "error",
      text: "连接失败",
    });
    assert.equal(context.opened.length, 0);
  }

  {
    const context = setup();
    const pending = followMarkdownLink("session", "app.py");
    globalThis.fetch = async () => Response.json({ detail: "无法显示文件" }, { status: 400 });
    context.settle(revealVerdict);
    const note = await pending;
    assert.equal(note?.kind, "error");
    assert.match(note?.text ?? "", /无法显示文件/);
    assert.equal(context.opened.length, 0);
  }
} finally {
  globalThis.fetch = originalFetch;
  if (originalWindow === undefined) Reflect.deleteProperty(globalThis, "window");
  else globalThis.window = originalWindow;
}

console.log("Markdown 链接行为测试通过");
