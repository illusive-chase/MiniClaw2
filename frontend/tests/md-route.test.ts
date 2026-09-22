import assert from "node:assert/strict";

import {
    classifyHref,
    markdownUrlTransform,
    parseMarkdownRoute,
    markdownRouteUrl,
} from "../src/markdownRoute";

// markdownRouteUrl reads window.location.pathname.
(globalThis as unknown as { window: { location: { pathname: string } } }).window = {
    location: { pathname: "/" },
};

// classifyHref: the frontend's one decision.
{
    assert.equal(classifyHref("https://example.com"), "external");
    assert.equal(classifyHref("http://example.com"), "external");
    assert.equal(classifyHref("mailto:a@b.c"), "external");
    assert.equal(classifyHref("#section"), "anchor");
    assert.equal(classifyHref("backend/app.py"), "local");
    assert.equal(classifyHref("../FUTURES.md"), "local");
    // file:// must NOT be treated as external — the browser refuses it from an
    // http origin, so it has to go through the backend like any local path.
    assert.equal(classifyHref("file:///Users/x/a.md"), "local");
    // A scheme-relative URL is the browser's business; on Unix the backend
    // would read it as an absolute filesystem path.
    assert.equal(classifyHref("//example.com/docs"), "external");
    assert.equal(classifyHref("//example.com"), "external");
    assert.equal(classifyHref("   "), "local");
}

// markdownUrlTransform: `file:` must survive sanitization to reach the click
// handler, without reopening the schemes sanitization exists to block.
{
    assert.equal(
        markdownUrlTransform("file:///Users/x/a.md"),
        "file:///Users/x/a.md",
    );
    assert.equal(
        markdownUrlTransform("FILE:///Users/x/a.md"),
        "FILE:///Users/x/a.md",
    );
    // Untouched by us, and still permitted by the default.
    assert.equal(markdownUrlTransform("backend/app.py"), "backend/app.py");
    assert.equal(markdownUrlTransform("../FUTURES.md"), "../FUTURES.md");
    assert.equal(markdownUrlTransform("README.md:12"), "README.md%3A12");
    assert.equal(markdownUrlTransform("README.md:12:4"), "README.md%3A12%3A4");
    assert.equal(markdownUrlTransform("backend/app.py:12"), "backend/app.py:12");
    assert.equal(markdownUrlTransform("https://example.com"), "https://example.com");
    assert.equal(markdownUrlTransform("#anchor"), "#anchor");
    // Still blanked: the reason the default transform exists.
    assert.equal(markdownUrlTransform("javascript:alert(1)"), "");
}

// parseMarkdownRoute: the three shapes, plus junk.
{
    const artifact = parseMarkdownRoute(
        "#/md?src=artifact&session=s1&node=n1&name=report.md",
    );
    assert.deepEqual(artifact, {
        src: "artifact",
        sessionId: "s1",
        nodeId: "n1",
        name: "report.md",
    });

    const diff = parseMarkdownRoute(
        "#/diff?src=diff&session=s1&node=n1&name=run-diff.json",
    );
    assert.deepEqual(diff, {
        src: "diff",
        sessionId: "s1",
        nodeId: "n1",
        name: "run-diff.json",
    });

    const projectFile = parseMarkdownRoute("#/md?src=project-file&session=s1&path=FUTURES.md");
    assert.deepEqual(projectFile, {
        src: "project-file",
        sessionId: "s1",
        path: "FUTURES.md",
    });

    const stash = parseMarkdownRoute("#/md?src=stash&key=abc123");
    assert.deepEqual(stash, { src: "stash", key: "abc123" });

    // Not a markdown route.
    assert.equal(parseMarkdownRoute("#/other"), null);
    assert.equal(parseMarkdownRoute(""), null);
    assert.equal(parseMarkdownRoute("#/md"), null); // no query at all
    // Missing required params for each shape.
    assert.equal(parseMarkdownRoute("#/md?src=artifact&session=s1"), null);
    assert.equal(parseMarkdownRoute("#/md?src=project-file&session=s1"), null);
    assert.equal(parseMarkdownRoute("#/md?src=stash"), null);
    assert.equal(parseMarkdownRoute("#/md?src=bogus&x=1"), null);
}

// Round trip: a parsed route rebuilds to a hash that parses back the same.
{
    const routes = [
        { src: "artifact", sessionId: "s1", nodeId: "n1", name: "r e.md" },
        { src: "diff", sessionId: "s1", nodeId: "n1", name: "run-diff.json" },
        { src: "project-file", sessionId: "s1", path: "docs/a b.md" },
        { src: "stash", key: "k1" },
    ] as const;
    for (const route of routes) {
        const url = markdownRouteUrl(route);
        const hash = url.slice(url.indexOf("#"));
        assert.deepEqual(parseMarkdownRoute(hash), route);
    }
}

console.log("md-route: ok");
