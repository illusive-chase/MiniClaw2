import assert from "node:assert/strict";

import {
  WAITING_ONLY_STORAGE_KEY,
  barTone,
  formatElapsed,
  isHumanBlocked,
  readWaitingOnly,
  rowContext,
  sortActiveEntries,
  summarize,
  writeWaitingOnly,
} from "../src/activeNodes";
import type { ActiveNodeEntry, NodeState } from "../src/types";

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

/* ---- summary and bar tone ---- */

{
  const summary = summarize([
    entry("1", "waiting"),
    entry("2", "awaiting_human_input"),
    entry("3", "running"),
    entry("4", "queued"),
    entry("5", "error"),
  ]);
  assert.deepEqual(summary, { waiting: 2, running: 1, queued: 1, error: 1 });
}

{
  assert.equal(barTone(summarize([])), null, "silent when nothing is active");
  assert.equal(barTone(summarize([entry("1", "running")])), "running");
  assert.equal(barTone(summarize([entry("1", "queued")])), "running");
  assert.equal(barTone(summarize([entry("1", "error")])), "error");
  assert.equal(
    barTone(summarize([entry("1", "error"), entry("2", "waiting")])),
    "waiting",
    "a human blocked outranks an error for the bar color",
  );
  assert.equal(
    barTone(summarize([entry("1", "running"), entry("2", "error")])),
    "error",
    "an error outranks mere running",
  );
}

{
  assert.equal(isHumanBlocked(entry("1", "waiting")), true);
  assert.equal(isHumanBlocked(entry("1", "awaiting_human_input")), true);
  assert.equal(isHumanBlocked(entry("1", "running")), false);
  assert.equal(isHumanBlocked(entry("1", "error")), false);
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

  /* An error is terminal. Measuring from started_at against the live clock
   * would render a dead node as still running, forever. */
  assert.equal(
    rowContext(entry("1", "error", { started_at: at(9999), finished_at: at(75) }), now),
    "1m15s前失败",
    "an error reports time since failure, not since start",
  );
  assert.equal(
    rowContext(entry("1", "error", { started_at: at(9999), finished_at: null }), now),
    "已失败",
    "an error with no finish timestamp still must not read as running",
  );
}

/* ---- filter persistence degrades without localStorage ---- */

{
  const store = new Map<string, string>();
  (globalThis as { window?: unknown }).window = {
    localStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
    },
  };
  assert.equal(readWaitingOnly(), false, "defaults to showing everything");
  writeWaitingOnly(true);
  assert.equal(store.get(WAITING_ONLY_STORAGE_KEY), "1");
  assert.equal(readWaitingOnly(), true);
  writeWaitingOnly(false);
  assert.equal(readWaitingOnly(), false);

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
  assert.equal(readWaitingOnly(), false, "read falls back instead of throwing");
  assert.doesNotThrow(() => writeWaitingOnly(true), "write swallows failure");
}

console.log("active-nodes tests passed");
