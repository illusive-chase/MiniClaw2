import assert from "node:assert/strict";

import {
    HANDOFF_LIMITS,
    readStashedMarkdown,
    stashMarkdown,
} from "../src/mdHandoff";

/** Minimal Storage stand-in; `failWrites` models quota/private mode. */
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

type Stores = { local: FakeStorage | null; session: FakeStorage | null };

/** Install both stores. `null` models a store whose access itself throws. */
function install({ local, session }: Stores): void {
    const target = globalThis as unknown as { window: unknown };
    target.window = {
        get localStorage(): FakeStorage {
            if (!local) throw new Error("blocked");
            return local;
        },
        get sessionStorage(): FakeStorage {
            if (!session) throw new Error("blocked");
            return session;
        },
    };
}

/** A fresh pair, as two different tabs would see them. */
function tabs(): { local: FakeStorage; session: FakeStorage } {
    return { local: new FakeStorage(), session: new FakeStorage() };
}

// A round trip preserves the payload, including the session id.
{
    const { local, session } = tabs();
    install({ local, session });
    const key = stashMarkdown({
        title: "结果摘要",
        subtitle: "node abc",
        text: "# hello",
        linkBase: { kind: "project-root" },
        sessionId: "s1",
    });
    assert.ok(key);
    const got = readStashedMarkdown(key!);
    assert.equal(got?.text, "# hello");
    assert.equal(got?.title, "结果摘要");
    assert.equal(got?.sessionId, "s1");
    assert.deepEqual(got?.linkBase, { kind: "project-root" });
}

// The reading tab has its OWN sessionStorage — the writer's is never cloned,
// because these tabs are opened with `noopener`. The transfer must survive it.
{
    const writer = tabs();
    install(writer);
    const key = stashMarkdown({ title: "t", text: "body", sessionId: "s1" })!;

    // A different tab: same localStorage, a session store of its own.
    const readerSession = new FakeStorage();
    install({ local: writer.local, session: readerSession });
    const got = readStashedMarkdown(key);
    assert.equal(got?.text, "body");
    assert.equal(got?.sessionId, "s1");
}

// Reading adopts the record into the reading tab, so a reload still finds it
// — and the transfer copy is dropped once it has served its purpose.
{
    const writer = tabs();
    install(writer);
    const key = stashMarkdown({ title: "t", text: "body" })!;

    const readerSession = new FakeStorage();
    install({ local: writer.local, session: readerSession });
    readStashedMarkdown(key);

    assert.equal(writer.local.getItem(`${HANDOFF_LIMITS.KEY_PREFIX}${key}`), null);
    assert.ok(readerSession.getItem(`${HANDOFF_LIMITS.TAB_PREFIX}${key}`));

    // A reload of that same tab: the transfer copy is gone, the adopted one
    // answers, and it does so past the transfer window's expiry.
    const later = Date.now() + HANDOFF_LIMITS.MAX_AGE_MS * 10;
    assert.equal(readStashedMarkdown(key, later)?.text, "body");
}

// A failed write returns null so the caller does not open a blank tab.
{
    const { local, session } = tabs();
    local.failWrites = true;
    install({ local, session });
    assert.equal(stashMarkdown({ title: "t", text: "x" }), null);
}

// Storage that throws on access degrades to "no handoff" rather than throwing.
{
    install({ local: null, session: null });
    assert.equal(stashMarkdown({ title: "t", text: "x" }), null);
    assert.equal(readStashedMarkdown("whatever"), null);
}

// A reading tab with no sessionStorage still gets the text; it just cannot
// adopt it, so the transfer copy has to stay.
{
    const writer = tabs();
    install(writer);
    const key = stashMarkdown({ title: "t", text: "body" })!;
    install({ local: writer.local, session: null });
    assert.equal(readStashedMarkdown(key)?.text, "body");
    assert.ok(writer.local.getItem(`${HANDOFF_LIMITS.KEY_PREFIX}${key}`));
}

// An expired transfer entry is not returned.
{
    const writer = tabs();
    install(writer);
    const key = stashMarkdown({ title: "t", text: "x" })!;
    const reader = { local: writer.local, session: new FakeStorage() };
    install(reader);
    const future = Date.now() + HANDOFF_LIMITS.MAX_AGE_MS + 1000;
    assert.equal(readStashedMarkdown(key, future), null);
}

// A missing key is not an error.
{
    install(tabs());
    assert.equal(readStashedMarkdown("nope"), null);
}

// The entry count stays bounded: this store is invisible to the user.
{
    const { local, session } = tabs();
    install({ local, session });
    for (let i = 0; i < HANDOFF_LIMITS.MAX_ENTRIES + 8; i += 1) {
        stashMarkdown({ title: `t${i}`, text: `body ${i}` });
    }
    const ours = [...local.map.keys()].filter((k) =>
        k.startsWith(HANDOFF_LIMITS.KEY_PREFIX),
    );
    assert.ok(
        ours.length <= HANDOFF_LIMITS.MAX_ENTRIES,
        `expected <= ${HANDOFF_LIMITS.MAX_ENTRIES}, got ${ours.length}`,
    );
}

// Unrelated localStorage keys are never touched — this one is shared with the
// rest of the app, so pruning must stay inside our own prefix.
{
    const { local, session } = tabs();
    install({ local, session });
    local.map.set("someone.elses.key", "keep me");
    for (let i = 0; i < HANDOFF_LIMITS.MAX_ENTRIES + 4; i += 1) {
        stashMarkdown({ title: `t${i}`, text: "x" });
    }
    assert.equal(local.getItem("someone.elses.key"), "keep me");
}

// A corrupt record reads as absent instead of throwing.
{
    const { local, session } = tabs();
    install({ local, session });
    local.map.set(`${HANDOFF_LIMITS.KEY_PREFIX}bad`, "{not json");
    assert.equal(readStashedMarkdown("bad"), null);
    local.map.set(`${HANDOFF_LIMITS.KEY_PREFIX}bad2`, JSON.stringify({ title: 1 }));
    assert.equal(readStashedMarkdown("bad2"), null);
}

console.log("md-handoff: ok");
