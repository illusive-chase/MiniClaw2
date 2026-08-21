import assert from "node:assert/strict";

import {
  ACTIVE_NODES_POLL_MS,
  READ_KEYS_STORAGE_KEY,
  UNREAD_ONLY_STORAGE_KEY,
  badgeCountLabel,
  badgeTone,
  formatElapsed,
  isHumanBlocked,
  isTerminal,
  isVerticallyVisible,
  notificationKey,
  pruneReadKeys,
  readReadKeys,
  readUnreadOnly,
  resolveReadKeys,
  rowContext,
  sortActiveEntries,
  sortFeed,
  summarize,
  unreadEntries,
  writeReadKeys,
  writeUnreadOnly,
} from "../src/activeNodes";
import type { ActiveNodeEntry, NodeState } from "../src/types";

assert.equal(ACTIVE_NODES_POLL_MS, 15_000);

function entry(
  nodeId: string,
  state: NodeState,
  opts: {
    started_at?: number;
    finished_at?: number | null;
    project_id?: string;
    gate?: ActiveNodeEntry["gate"];
  } = {},
): ActiveNodeEntry {
  return {
    project_id: opts.project_id ?? "p1",
    project_name: "proj",
    node_id: nodeId,
    state,
    category: "regular",
    planspace_id: null,
    planspace_title: null,
    is_active_planspace: false,
    label: "",
    started_at: opts.started_at ?? 0,
    finished_at: opts.finished_at ?? null,
    gate: opts.gate ?? null,
  };
}

/** Swap in a working localStorage and clear it. */
function withStorage(): Map<string, string> {
  const store = new Map<string, string>();
  (globalThis as { window?: unknown }).window = {
    localStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
      removeItem: (key: string) => void store.delete(key),
    },
  };
  return store;
}

/* ---- urgency ordering ---- */

{
  const sorted = sortActiveEntries([
    entry("queued", "queued"),
    entry("running", "running"),
    entry("error", "error"),
    entry("waiting", "waiting"),
  ]);
  assert.deepEqual(
    sorted.map((item) => item.node_id),
    ["waiting", "error", "running", "queued"],
    "human-blocked outranks error, which outranks running, which outranks queued",
  );
}

{
  /* awaiting_human_input ranks with waiting: both mean a human is the blocker. */
  const sorted = sortActiveEntries([
    entry("running", "running"),
    entry("human", "awaiting_human_input"),
  ]);
  assert.deepEqual(sorted.map((item) => item.node_id), ["human", "running"]);
}

{
  /* Oldest first inside a group: the longest-blocked thing is the most
   * worth doing next. This is the opposite of the landing page's ordering. */
  const sorted = sortActiveEntries([
    entry("new", "waiting", { started_at: 3000 }),
    entry("old", "waiting", { started_at: 1000 }),
    entry("mid", "waiting", { started_at: 2000 }),
  ]);
  assert.deepEqual(sorted.map((item) => item.node_id), ["old", "mid", "new"]);
}

{
  const input = [entry("b", "running"), entry("a", "running")];
  const sorted = sortActiveEntries(input);
  assert.deepEqual(sorted.map((item) => item.node_id), ["a", "b"], "stable tiebreak");
  assert.deepEqual(input.map((item) => item.node_id), ["b", "a"], "input not mutated");
}

/* ---- feed ordering: live above, finished below, opposite directions ---- */

{
  const rows = sortFeed([
    entry("done-old", "done", { finished_at: 1000 }),
    entry("running", "running", { started_at: 500 }),
    entry("done-new", "done", { finished_at: 3000 }),
    entry("waiting", "waiting", { started_at: 900 }),
    entry("cancelled", "cancelled", { finished_at: 2000 }),
  ]);
  assert.deepEqual(
    rows.map((item) => item.node_id),
    ["waiting", "running", "done-new", "cancelled", "done-old"],
    "live segment first (urgency, oldest-first); terminal segment newest-first",
  );
}

{
  /* An error is terminal and belongs in the history segment, even though the
   * live ranking would have placed it above running work. */
  const rows = sortFeed([
    entry("running", "running", { started_at: 100 }),
    entry("error", "error", { finished_at: 5000 }),
  ]);
  assert.deepEqual(rows.map((item) => item.node_id), ["running", "error"]);
}

{
  const input = [entry("b", "done", { finished_at: 1 }), entry("a", "running")];
  sortFeed(input);
  assert.deepEqual(input.map((item) => item.node_id), ["b", "a"], "input not mutated");
}

{
  assert.equal(isTerminal(entry("1", "done")), true);
  assert.equal(isTerminal(entry("1", "error")), true);
  assert.equal(isTerminal(entry("1", "cancelled")), true);
  assert.equal(isTerminal(entry("1", "running")), false);
  assert.equal(isTerminal(entry("1", "waiting")), false);
  assert.equal(isTerminal(entry("1", "queued")), false);
}

/* ---- viewport visibility: only rows the panel could show become read ---- */

{
  const viewport = { top: 100, bottom: 300 };
  assert.equal(isVerticallyVisible({ top: 100, bottom: 140 }, viewport), true);
  assert.equal(isVerticallyVisible({ top: 260, bottom: 320 }, viewport), true);
  assert.equal(isVerticallyVisible({ top: 0, bottom: 100 }, viewport), false);
  assert.equal(isVerticallyVisible({ top: 300, bottom: 340 }, viewport), false);
  assert.equal(
    isVerticallyVisible({ top: 140, bottom: 260 }, viewport),
    true,
    "a fully visible row is included",
  );
}

/* ---- event identity ---- */

{
  const node = "n1";
  assert.notEqual(
    notificationKey(entry(node, "running")),
    notificationKey(entry(node, "done")),
    "a node reaching done is a new event, distinct from having been running",
  );
  assert.equal(
    notificationKey(entry(node, "done", { finished_at: 100 })),
    notificationKey(entry(node, "done", { finished_at: 999 })),
    "seeing the same done again is not a new event, whatever the timestamp",
  );
  assert.notEqual(
    notificationKey(entry("n1", "done")),
    notificationKey(entry("n2", "done")),
  );
}

{
  const read = new Set([notificationKey(entry("seen", "done"))]);
  const unread = unreadEntries(
    [entry("seen", "done"), entry("fresh", "error"), entry("seen", "running")],
    read,
  );
  assert.deepEqual(
    unread.map((item) => `${item.node_id}:${item.state}`),
    ["fresh:error", "seen:running"],
    "read is per (node, state), so another state of a read node is still unread",
  );
}

/* ---- badge ---- */

{
  assert.equal(badgeTone([]), null, "no badge at all when nothing is unread");
  assert.equal(badgeTone([entry("1", "waiting")]), "waiting");
  assert.equal(badgeTone([entry("1", "awaiting_human_input")]), "waiting");
  assert.equal(badgeTone([entry("1", "error")]), "error");
  assert.equal(badgeTone([entry("1", "done")]), "done");
  assert.equal(badgeTone([entry("1", "cancelled")]), "done");
  assert.equal(
    badgeTone([entry("1", "running")]),
    "done",
    "running alone is informational: the icon dot carries it, not an alarm color",
  );
  assert.equal(
    badgeTone([entry("1", "done"), entry("2", "error"), entry("3", "waiting")]),
    "waiting",
    "waiting outranks error, which outranks merely-finished",
  );
  assert.equal(
    badgeTone([entry("1", "done"), entry("2", "error")]),
    "error",
    "an error outranks a completion",
  );
}

{
  assert.equal(badgeCountLabel(1), "1");
  assert.equal(badgeCountLabel(9), "9");
  assert.equal(badgeCountLabel(10), "9+", "width stays stable past single digits");
  assert.equal(badgeCountLabel(363), "9+");
}

/* ---- summary ---- */

{
  const summary = summarize([
    entry("1", "waiting"),
    entry("2", "awaiting_human_input"),
    entry("3", "running"),
    entry("4", "queued"),
    entry("5", "error"),
    entry("6", "done"),
    entry("7", "cancelled"),
  ]);
  assert.deepEqual(summary, {
    waiting: 2,
    running: 1,
    queued: 1,
    error: 1,
    done: 1,
    cancelled: 1,
  });
}

{
  assert.equal(isHumanBlocked(entry("1", "waiting")), true);
  assert.equal(isHumanBlocked(entry("1", "awaiting_human_input")), true);
  assert.equal(isHumanBlocked(entry("1", "running")), false);
  assert.equal(isHumanBlocked(entry("1", "error")), false);
  assert.equal(isHumanBlocked(entry("1", "done")), false);
}

/* ---- seeding: a first run must open at zero, not at the store's history ---- */

{
  const store = withStorage();
  const feed = [
    entry("a", "done", { finished_at: 10 }),
    entry("b", "cancelled", { finished_at: 20 }),
    entry("c", "waiting"),
  ];
  const seeded = resolveReadKeys(feed);
  assert.equal(seeded.size, 3, "a missing read set adopts the whole feed as read");
  assert.equal(
    unreadEntries(feed, seeded).length,
    0,
    "the badge opens at zero rather than counting every historical node",
  );
  assert.ok(store.has(READ_KEYS_STORAGE_KEY), "the seed is persisted, not just returned");
}

{
  /* A quiet workspace still seeds, and seeds to an empty set. Deferring the
   * seed until rows appear would make the user's first real notification the
   * thing that gets adopted as already-read. */
  const store = withStorage();
  const seeded = resolveReadKeys([]);
  assert.equal(seeded.size, 0);
  assert.equal(store.get(READ_KEYS_STORAGE_KEY), "[]", "an empty seed is persisted");
  /* And the next event after that seed is unread. */
  const later = [entry("first", "done", { finished_at: 10 })];
  assert.equal(unreadEntries(later, resolveReadKeys(later)).length, 1);
}

{
  /* Existing-but-empty is a user who cleared their read state. Re-seeding
   * would throw away exactly the state they asked for. */
  const store = withStorage();
  store.set(READ_KEYS_STORAGE_KEY, "[]");
  const feed = [entry("a", "done", { finished_at: 10 })];
  const resolved = resolveReadKeys(feed);
  assert.equal(resolved.size, 0, "an empty set is honored, not treated as missing");
  assert.equal(unreadEntries(feed, resolved).length, 1);
}

{
  const store = withStorage();
  store.set(READ_KEYS_STORAGE_KEY, JSON.stringify(["n1:done"]));
  const resolved = resolveReadKeys([entry("n1", "done"), entry("n2", "error")]);
  assert.deepEqual([...resolved], ["n1:done"], "a stored set is used as-is");
}

{
  /* Corrupt JSON must not seed: silently adopting the feed as read would hide
   * notifications the user never saw. */
  const store = withStorage();
  store.set(READ_KEYS_STORAGE_KEY, "{not json");
  const feed = [entry("a", "done", { finished_at: 1 })];
  assert.equal(resolveReadKeys(feed).size, 0);
  assert.equal(unreadEntries(feed, resolveReadKeys(feed)).length, 1);
}

{
  const store = withStorage();
  store.set(READ_KEYS_STORAGE_KEY, JSON.stringify(["ok", 42, null]));
  assert.deepEqual([...(readReadKeys() ?? [])], ["ok"], "non-strings are dropped");
}

/* ---- pruning keeps anything still on screen ---- */

{
  const feedKeys = new Set(["keep:done", "keep2:done"]);
  const keys = new Set(["gone1:done", "gone2:done", "keep:done", "keep2:done"]);
  const pruned = pruneReadKeys(keys, feedKeys, 3);
  assert.equal(pruned.size, 3);
  assert.ok(pruned.has("keep:done") && pruned.has("keep2:done"), "feed keys survive");
  assert.ok(!pruned.has("gone1:done"), "the oldest out-of-feed key is evicted first");
  assert.ok(pruned.has("gone2:done"));
}

{
  const keys = new Set(["a", "b"]);
  const pruned = pruneReadKeys(keys, new Set(), 10);
  assert.deepEqual([...pruned], ["a", "b"], "under the cap nothing is dropped");
  assert.notEqual(pruned, keys, "a fresh set is returned rather than the input mutated");
}

{
  /* If the feed alone exceeds the cap, feed keys still win: evicting one would
   * make an already-read row resurface as unread. */
  const feedKeys = new Set(["f1", "f2", "f3"]);
  const pruned = pruneReadKeys(new Set(["old", "f1", "f2", "f3"]), feedKeys, 2);
  assert.deepEqual([...pruned].sort(), ["f1", "f2", "f3"]);
}

/* ---- elapsed formatting ---- */

{
  const now = 10_000_000;
  const at = (secondsAgo: number) => now / 1000 - secondsAgo;
  assert.equal(formatElapsed(at(5), now), "5s");
  assert.equal(formatElapsed(at(75), now), "1m15s");
  assert.equal(formatElapsed(at(3800), now), "1h03m");
  assert.equal(formatElapsed(null, now), "", "no start time renders nothing");
  assert.equal(
    formatElapsed(at(-5), now),
    "0s",
    "clock skew must not render a negative duration",
  );
}

/* ---- row context ---- */

{
  const now = 10_000_000;
  const at = (secondsAgo: number) => now / 1000 - secondsAgo;

  assert.equal(
    rowContext(entry("1", "running", { started_at: at(75) }), now),
    "已跑 1m15s",
    "a running node reports how long it has been running",
  );
  assert.equal(rowContext(entry("1", "queued"), now), "等待槽位");
  assert.equal(
    rowContext(
      entry("1", "waiting", {
        gate: { id: "g1", subtype: "permission", tool_name: "Write", summary: "/tmp/x" },
      }),
      now,
    ),
    "▸ /tmp/x",
    "an open gate outranks any duration",
  );

  /* Terminal nodes are measured from when they ended. Measuring from
   * started_at against the live clock would render a dead node as still
   * running, forever. */
  assert.equal(
    rowContext(entry("1", "error", { started_at: at(9999), finished_at: at(75) }), now),
    "1m15s前失败",
    "an error reports time since failure, not since start",
  );
  assert.equal(
    rowContext(entry("1", "done", { started_at: at(9999), finished_at: at(75) }), now),
    "1m15s前完成",
  );
  assert.equal(
    rowContext(entry("1", "cancelled", { started_at: at(9999), finished_at: at(75) }), now),
    "1m15s前取消",
  );
  assert.equal(
    rowContext(entry("1", "error", { started_at: at(9999), finished_at: null }), now),
    "已失败",
    "an error with no finish timestamp still must not read as running",
  );
  assert.equal(
    rowContext(entry("1", "done", { started_at: at(9999), finished_at: null }), now),
    "已完成",
  );
}

/* ---- persistence degrades without localStorage ---- */

{
  const store = withStorage();
  assert.equal(readUnreadOnly(), false, "defaults to showing everything");
  writeUnreadOnly(true);
  assert.equal(store.get(UNREAD_ONLY_STORAGE_KEY), "1");
  assert.equal(readUnreadOnly(), true);
  writeUnreadOnly(false);
  assert.equal(readUnreadOnly(), false);

  writeReadKeys(new Set(["n1:done"]));
  assert.deepEqual([...(readReadKeys() ?? [])], ["n1:done"], "read keys round-trip");
}

{
  (globalThis as { window?: unknown }).window = {
    localStorage: {
      getItem: () => {
        throw new Error("storage disabled");
      },
      setItem: () => {
        throw new Error("storage disabled");
      },
    },
  };
  assert.equal(readUnreadOnly(), false, "read falls back instead of throwing");
  assert.doesNotThrow(() => writeUnreadOnly(true), "write swallows failure");
  assert.doesNotThrow(() => writeReadKeys(new Set(["x"])), "read-key write swallows too");
  /* Unavailable storage must behave like corrupt storage, not like a first
   * run: seeding here would mark the feed read on every single reload. */
  const feed = [entry("a", "done", { finished_at: 1 })];
  assert.equal(readReadKeys()?.size, 0);
  assert.equal(unreadEntries(feed, resolveReadKeys(feed)).length, 1);
}

console.log("active-nodes tests passed");
