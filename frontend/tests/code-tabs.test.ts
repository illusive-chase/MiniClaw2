import assert from "node:assert/strict";

import {
    EMPTY_TABS,
    activateTab,
    closeTab,
    isNavEntry,
    openTab,
    pinTab,
    recordPosition,
    retainTabs,
    tabTitles,
    type TabsState,
} from "../src/codeTabs";

const paths = (state: TabsState) => state.tabs.map((tab) => `${tab.preview ? "~" : ""}${tab.path}`);

// Tree click: preview tab; a second preview click replaces it in place.
{
    let state = openTab(EMPTY_TABS, "a.py", { preview: true });
    assert.deepEqual(paths(state), ["~a.py"]);
    state = openTab(state, "b.py", { preview: true });
    assert.deepEqual(paths(state), ["~b.py"]);
    assert.equal(state.active, "b.py");
    assert.deepEqual(state.mru, ["b.py"]);
}

// Double click / Enter: pinned; an open preview becomes pinned.
{
    let state = openTab(EMPTY_TABS, "a.py", { preview: false });
    state = openTab(state, "b.py", { preview: true });
    state = pinTab(state, "b.py");
    assert.deepEqual(paths(state), ["a.py", "b.py"]);
    state = openTab(state, "c.py", { preview: true });
    assert.deepEqual(paths(state), ["a.py", "b.py", "~c.py"]);
}

// New tabs insert to the right of the active tab.
{
    let state = openTab(EMPTY_TABS, "a.py", { preview: false });
    state = openTab(state, "b.py", { preview: false });
    state = activateTab(state, "a.py");
    state = openTab(state, "c.py", { preview: false });
    assert.deepEqual(paths(state), ["a.py", "c.py", "b.py"]);
}

// Jump from a preview tab to another file: the target replaces the origin.
{
    let state = openTab(EMPTY_TABS, "pinned.py", { preview: false });
    state = openTab(state, "origin.py", { preview: true });
    state = openTab(state, "target.py", { preview: true, position: { topLine: 40 } });
    assert.deepEqual(paths(state), ["pinned.py", "~target.py"]);
    assert.equal(state.tabs[1].topLine, 40);
}

// Jump to an already-open file: reuse it, move it, keep its pin state.
{
    let state = openTab(EMPTY_TABS, "a.py", { preview: false, position: { topLine: 10, scrollTop: 100 } });
    state = openTab(state, "b.py", { preview: true });
    state = openTab(state, "a.py", { preview: true, position: { topLine: 99 } });
    assert.deepEqual(paths(state), ["a.py", "~b.py"]);
    assert.equal(state.active, "a.py");
    assert.equal(state.tabs[0].topLine, 99);
    assert.equal(state.tabs[0].scrollTop, 100);
    assert.equal(state.tabs.length, 2);
}

// Closing the active tab activates the most recently used one.
{
    let state = openTab(EMPTY_TABS, "a.py", { preview: false });
    state = openTab(state, "b.py", { preview: false });
    state = openTab(state, "c.py", { preview: false });
    state = activateTab(state, "a.py");
    state = activateTab(state, "c.py");
    state = closeTab(state, "c.py");
    assert.equal(state.active, "a.py");
    state = closeTab(state, "b.py");
    assert.equal(state.active, "a.py");
    assert.deepEqual(paths(state), ["a.py"]);
    state = closeTab(state, "a.py");
    assert.equal(state.active, null);
}

// Back to a closed file reopens it as a preview.
{
    let state = openTab(EMPTY_TABS, "a.py", { preview: false });
    state = openTab(state, "b.py", { preview: false });
    state = closeTab(state, "a.py");
    state = openTab(state, "a.py", { preview: true, position: { scrollTop: 300 } });
    assert.deepEqual(paths(state), ["b.py", "~a.py"]);
    assert.equal(state.tabs[1].scrollTop, 300);
}

// Positions and restore.
{
    let state = openTab(EMPTY_TABS, "a.py", { preview: false });
    const same = recordPosition(state, "a.py", { scrollTop: 0, topLine: 1 });
    assert.equal(same, state);
    state = recordPosition(state, "a.py", { scrollTop: 50, topLine: 3 });
    assert.equal(state.tabs[0].scrollTop, 50);
    const restored = retainTabs(
        { tabs: [...state.tabs, { path: "gone.py", preview: true, scrollTop: 0, topLine: 1 }], active: "gone.py", mru: ["gone.py", "a.py"] },
        (path) => path !== "gone.py",
    );
    assert.deepEqual(paths(restored), ["a.py"]);
    assert.equal(restored.active, "a.py");
}

// History entries and titles.
{
    assert.ok(isNavEntry({ kind: "code-nav", seq: 1, path: "a.py", scrollTop: 0, line: null }));
    assert.ok(!isNavEntry(null));
    assert.ok(!isNavEntry({ kind: "other", seq: 1, path: "a.py" }));
    const titles = tabTitles(["src/pages/index.ts", "src/api/index.ts", "lib/pages/index.ts", "README.md"]);
    assert.deepEqual(titles.get("README.md"), { name: "README.md", hint: "" });
    assert.equal(titles.get("src/api/index.ts")?.hint, "api");
    assert.equal(titles.get("src/pages/index.ts")?.hint, "src/pages");
    assert.equal(titles.get("lib/pages/index.ts")?.hint, "lib/pages");
}

console.log("code-tabs ok");
