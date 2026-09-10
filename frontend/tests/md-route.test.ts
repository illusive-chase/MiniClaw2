import assert from "node:assert/strict";

import {
    classifyHref,
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
    assert.equal(classifyHref("   "), "local");
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
