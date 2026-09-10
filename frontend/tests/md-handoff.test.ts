import assert from "node:assert/strict";

import {
    HANDOFF_LIMITS,
    readStashedMarkdown,
    stashMarkdown,
} from "../src/mdHandoff";

/** Minimal sessionStorage stand-in; `failWrites` models quota/private mode. */
class FakeStorage {
    map = new Map<string, string>();
    failWrites = false;
    get length() {
        return this.map.size;
    }
    key(i: number): string | null {
        return [...this.map.keys()][i] ?? null;
    }
    getItem(k: string): string | null {
        return this.map.get(k) ?? null;
    }
    setItem(k: string, v: string): void {
        if (this.failWrites) throw new Error("QuotaExceededError");
        this.map.set(k, v);
    }
    removeItem(k: string): void {
        this.map.delete(k);
    }
}

function install(store: FakeStorage | null): void {
    const target = globalThis as unknown as { window: unknown };
    if (store === null) {
        // Accessing sessionStorage itself throws when site data is blocked.
        target.window = {
            get sessionStorage(): never {
                throw new Error("blocked");
            },
        };
        return;
    }
    target.window = { sessionStorage: store };
}

// A round trip preserves the payload.
{
    const store = new FakeStorage();
    install(store);
    const key = stashMarkdown({
        title: "结果摘要",
        subtitle: "node abc",
        text: "# hello",
        linkBase: { kind: "project-root" },
    });
    assert.ok(key);
    const got = readStashedMarkdown(key!);
    assert.equal(got?.text, "# hello");
    assert.equal(got?.title, "结果摘要");
    assert.deepEqual(got?.linkBase, { kind: "project-root" });
}

// A failed write returns null so the caller does not open a blank tab.
{
    const store = new FakeStorage();
    store.failWrites = true;
    install(store);
    assert.equal(stashMarkdown({ title: "t", text: "x" }), null);
}

// Storage that throws on access degrades to "no handoff" rather than throwing.
{
    install(null);
    assert.equal(stashMarkdown({ title: "t", text: "x" }), null);
    assert.equal(readStashedMarkdown("whatever"), null);
}

// An expired entry is not returned.
{
    const store = new FakeStorage();
    install(store);
    const key = stashMarkdown({ title: "t", text: "x" })!;
    const future = Date.now() + HANDOFF_LIMITS.MAX_AGE_MS + 1000;
    assert.equal(readStashedMarkdown(key, future), null);
}

// A missing key is not an error.
{
    const store = new FakeStorage();
    install(store);
    assert.equal(readStashedMarkdown("nope"), null);
}

// The entry count stays bounded: this store is invisible to the user.
{
    const store = new FakeStorage();
    install(store);
    for (let i = 0; i < HANDOFF_LIMITS.MAX_ENTRIES + 8; i += 1) {
        stashMarkdown({ title: `t${i}`, text: `body ${i}` });
    }
    const ours = [...store.map.keys()].filter((k) =>
        k.startsWith(HANDOFF_LIMITS.KEY_PREFIX),
    );
    assert.ok(
        ours.length <= HANDOFF_LIMITS.MAX_ENTRIES,
        `expected <= ${HANDOFF_LIMITS.MAX_ENTRIES}, got ${ours.length}`,
    );
}

// Unrelated sessionStorage keys are never touched.
{
    const store = new FakeStorage();
    install(store);
    store.map.set("someone.elses.key", "keep me");
    for (let i = 0; i < HANDOFF_LIMITS.MAX_ENTRIES + 4; i += 1) {
        stashMarkdown({ title: `t${i}`, text: "x" });
    }
    assert.equal(store.getItem("someone.elses.key"), "keep me");
}

// A corrupt record reads as absent instead of throwing.
{
    const store = new FakeStorage();
    install(store);
    store.map.set(`${HANDOFF_LIMITS.KEY_PREFIX}bad`, "{not json");
    assert.equal(readStashedMarkdown("bad"), null);
    store.map.set(`${HANDOFF_LIMITS.KEY_PREFIX}bad2`, JSON.stringify({ title: 1 }));
    assert.equal(readStashedMarkdown("bad2"), null);
}

console.log("md-handoff: ok");
