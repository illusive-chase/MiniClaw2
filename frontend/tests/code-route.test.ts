import assert from "node:assert/strict";

import { codeRouteUrl, codeWindowName, parseCodeRoute } from "../src/codeRoute";

(globalThis as unknown as { window: { location: { pathname: string } } }).window = {
    location: { pathname: "/" },
};

assert.deepEqual(parseCodeRoute("#/code?session=p1"), { sessionId: "p1" });
assert.deepEqual(parseCodeRoute("/code?session=a%20b"), { sessionId: "a b" });
assert.equal(parseCodeRoute("#/code"), null);
assert.equal(parseCodeRoute("#/code?session="), null);
assert.equal(parseCodeRoute("#/codex?session=p1"), null);
assert.equal(parseCodeRoute("#/md?src=stash&key=k"), null);
assert.equal(parseCodeRoute(""), null);

const url = codeRouteUrl("p 1");
assert.equal(url, "/#/code?session=p+1");
assert.deepEqual(parseCodeRoute(url.slice(url.indexOf("#"))), { sessionId: "p 1" });
assert.equal(codeWindowName("p1"), "miniclaw2-code-p1");

console.log("code-route ok");
