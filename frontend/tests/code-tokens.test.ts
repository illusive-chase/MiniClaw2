import assert from "node:assert/strict";

import { common, createLowlight } from "lowlight";

import { decodeAnalysis, hastToLines, languageFor, type HastRoot } from "../src/codeTokens";
import type { CodeAnalysis } from "../src/types";

// Relative decoding: Δline, Δcol from the previous token start on the same line.
{
    const analysis: CodeAnalysis = {
        v: 1, path: "m.py", sha256: "x", syntax_error: null,
        // line 1: [0,3) keyword, [4,5) function decl with ref 0; line 3: [2,6) variable
        tokens: [0, 0, 3, 0, 0, 0, 4, 1, 8, 1, 2, 2, 4, 12, 0],
        refs: [1, 0],
        symbols: [{
            kind: "function", name: "f", qualname: "m.f", signature: "(function) f()",
            type: null, doc: null, target: null, external: false,
        }],
    };
    const lines = decodeAnalysis(analysis, 3);
    assert.deepEqual(lines[0], [
        { start: 0, end: 3, cls: "tok-keyword" },
        { start: 4, end: 5, cls: "tok-function tok-decl", ref: 0 },
    ]);
    assert.deepEqual(lines[1], []);
    assert.deepEqual(lines[2], [{ start: 2, end: 6, cls: "tok-variable" }]);
}

// An uncoloured but hoverable token (type -1) keeps its ref.
{
    const analysis: CodeAnalysis = {
        v: 1, path: "m.py", sha256: "x", syntax_error: null,
        tokens: [0, 3, 4, -1, 2], refs: [0, 0],
        symbols: [{ kind: "variable", name: "path", qualname: "os.path", signature: "(name) os.path", type: null, doc: null, target: null, external: true }],
    };
    assert.deepEqual(decodeAnalysis(analysis, 1)[0], [{ start: 3, end: 7, cls: "", ref: 0 }]);
}

// lowlight hast splits per line; a multi-line string closes and reopens.
{
    const lowlight = createLowlight(common);
    const text = 'x = """a\nb"""  # c\nlen(x)\n';
    const lines = hastToLines(lowlight.highlight("python", text) as unknown as HastRoot, 3);
    assert.deepEqual(lines[0].find((s) => s.cls === "hljs-string"), { start: 4, end: 8, cls: "hljs-string" });
    assert.deepEqual(lines[1].find((s) => s.cls === "hljs-string"), { start: 0, end: 4, cls: "hljs-string" });
    assert.deepEqual(lines[1].find((s) => s.cls === "hljs-comment"), { start: 6, end: 9, cls: "hljs-comment" });
    assert.deepEqual(lines[2][0], { start: 0, end: 3, cls: "hljs-built_in" });
    // Nested classes keep the innermost one, joined as hljs emits them.
    const ts = hastToLines(lowlight.highlight("typescript", "function f() {}\n") as unknown as HastRoot, 1);
    assert.ok(ts[0].some((s) => s.cls === "hljs-title.function_"));
}

// UTF-16 offsets: an emoji counts two code units, matching JS indices.
{
    const lowlight = createLowlight(common);
    const text = 's = "😀"; t = 1\n';
    const lines = hastToLines(lowlight.highlight("python", text) as unknown as HastRoot, 1);
    const number = lines[0].find((s) => s.cls === "hljs-number");
    assert.ok(number);
    assert.equal(text.slice(number.start, number.end), "1");
}

// Languages by extension and by name.
{
    assert.equal(languageFor("a/b.py"), "python");
    assert.equal(languageFor("x.tsx"), "typescript");
    assert.equal(languageFor("pyproject.toml"), "ini");
    assert.equal(languageFor("docs/README.md"), "markdown");
    assert.equal(languageFor("Makefile"), "makefile");
    assert.equal(languageFor("LICENSE"), null);
    assert.equal(languageFor(".gitignore"), null);
}

console.log("code-tokens ok");
