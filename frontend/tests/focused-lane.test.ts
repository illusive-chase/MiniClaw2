import assert from "node:assert/strict";
import {
  FOCUSED_LANE_STORAGE_KEY,
  normalizeFocusedLanes,
  readFocusedLane,
  readFocusedLanes,
  resolveFocusedLane,
  writeFocusedLane,
  writeFocusedLanes,
} from "../src/focusedLane";
import { nodeLaneResolver } from "../src/canvas/layout";
import { lanesByRecentActivity } from "../src/nodeUtil";
import type { NodeInfo } from "../src/types";

type FakeStorage = {
  value: string | null;
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
};

function installStorage(storage: FakeStorage): void {
  Object.defineProperty(globalThis, "window", {
    value: { localStorage: storage },
    configurable: true,
    writable: true,
  });
}

function memoryStorage(initial: string | null = null): FakeStorage {
  return {
    value: initial,
    getItem(key) {
      assert.equal(key, FOCUSED_LANE_STORAGE_KEY);
      return this.value;
    },
    setItem(key, value) {
      assert.equal(key, FOCUSED_LANE_STORAGE_KEY);
      this.value = value;
    },
  };
}

function node(overrides: Partial<NodeInfo> & { id: string }): NodeInfo {
  return {
    project_id: "p",
    kind: "agent",
    state: "done",
    provider: null,
    prompt: "",
    created_at: 0,
    ...overrides,
  } as NodeInfo;
}

/* The four-step ladder is the whole contract of this module, and step 2 is
 * scaffolding: Phase 3 deletes `active`, so every step below it has to be a
 * correct standalone answer today, not a placeholder. */
function testResolutionPrefersTheMostDeliberateSignal(): void {
  const visible = ["lane.a", "lane.b", "lane.c"];

  assert.equal(
    resolveFocusedLane({
      stored: "lane.c",
      active: "lane.a",
      visible,
      recentlyActive: ["lane.b"],
    }),
    "lane.c",
    "an explicit past choice outranks every inference",
  );

  assert.equal(
    resolveFocusedLane({
      stored: null,
      active: "lane.a",
      visible,
      recentlyActive: ["lane.b"],
    }),
    "lane.a",
    "the backend's execution target is the compatibility fallback",
  );

  assert.equal(
    resolveFocusedLane({
      stored: null,
      active: null,
      visible,
      recentlyActive: ["lane.b", "lane.c"],
    }),
    "lane.b",
    "recency is what remains once the active field is gone",
  );

  assert.equal(
    resolveFocusedLane({
      stored: null,
      active: null,
      visible,
      recentlyActive: [],
    }),
    "lane.a",
    "a project with no runs still needs somewhere to put the +",
  );

  assert.equal(
    resolveFocusedLane({
      stored: "lane.a",
      active: "lane.b",
      visible: [],
      recentlyActive: ["lane.c"],
    }),
    null,
    "no visible lane means no focus at all",
  );
}

/* Focus must never point at a lane that draws nothing. A stale stored id
 * (lane deleted) and a hidden lane are the same failure from the user's
 * side: the `+` and the double-click target vanish with no explanation. */
function testResolutionSkipsLanesThatCannotBeSeen(): void {
  assert.equal(
    resolveFocusedLane({
      stored: "lane.deleted",
      active: null,
      visible: ["lane.a"],
      recentlyActive: [],
    }),
    "lane.a",
    "a stored lane that no longer exists is skipped, not returned",
  );

  assert.equal(
    resolveFocusedLane({
      stored: "lane.hidden",
      active: "lane.hidden",
      visible: ["lane.a", "lane.b"],
      recentlyActive: ["lane.b"],
    }),
    "lane.b",
    "hidden lanes are absent from `visible`, so both top steps fall through",
  );

  assert.equal(
    resolveFocusedLane({
      stored: null,
      active: null,
      visible: ["lane.a"],
      recentlyActive: ["lane.gone", "lane.a"],
    }),
    "lane.a",
    "recency entries are filtered against visibility too",
  );
}

function testStoredLanesAreScopedPerProject(): void {
  installStorage(memoryStorage());
  assert.deepEqual(readFocusedLanes(), {});
  assert.equal(readFocusedLane("proj-a"), null);

  writeFocusedLane("proj-a", "lane.one");
  writeFocusedLane("proj-b", "lane.two");
  assert.equal(readFocusedLane("proj-a"), "lane.one");
  assert.equal(readFocusedLane("proj-b"), "lane.two");

  /* Read-modify-write, so recording one project's focus must not discard
   * what another project had. */
  writeFocusedLane("proj-a", "lane.three");
  assert.equal(readFocusedLane("proj-a"), "lane.three");
  assert.equal(readFocusedLane("proj-b"), "lane.two");

  writeFocusedLane("proj-a", null);
  assert.equal(readFocusedLane("proj-a"), null);
  assert.equal(readFocusedLane("proj-b"), "lane.two");

  /* A missing project id is a no-op rather than a crash: the canvas can
   * render for a moment before a session exists. */
  assert.doesNotThrow(() => writeFocusedLane(null, "lane.x"));
  assert.doesNotThrow(() => writeFocusedLane(undefined, "lane.x"));
  assert.equal(readFocusedLane(null), null);
}

function testCorruptRecordsDegradeToNoMemory(): void {
  for (const corrupt of [null, 42, "nope", [], { a: 1 }, { a: "" }]) {
    assert.deepEqual(
      normalizeFocusedLanes(corrupt),
      {},
      `unexpected map for ${JSON.stringify(corrupt)}`,
    );
  }
  assert.deepEqual(normalizeFocusedLanes({ p: "lane", q: 7, "": "lane" }), {
    p: "lane",
  });

  installStorage(memoryStorage("{not json"));
  assert.deepEqual(readFocusedLanes(), {});
  assert.equal(readFocusedLane("proj-a"), null);
}

/* localStorage throws outright in some contexts (private windows, blocked
 * site data). Focus is a convenience; losing it must never take the canvas
 * down with it. */
function testStorageFailuresAreContained(): void {
  installStorage({
    value: null,
    getItem() {
      throw new Error("storage unavailable");
    },
    setItem() {
      throw new Error("storage unavailable");
    },
  });
  assert.doesNotThrow(() => readFocusedLanes());
  assert.deepEqual(readFocusedLanes(), {});
  assert.doesNotThrow(() => readFocusedLane("proj-a"));
  assert.equal(readFocusedLane("proj-a"), null);
  assert.doesNotThrow(() => writeFocusedLane("proj-a", "lane.one"));
  assert.doesNotThrow(() => writeFocusedLanes({ "proj-a": "lane.one" }));

  /* And resolution still works with no storage at all — it just starts at
   * the `active`/recency steps instead. */
  assert.equal(
    resolveFocusedLane({
      stored: readFocusedLane("proj-a"),
      active: null,
      visible: ["lane.a", "lane.b"],
      recentlyActive: ["lane.b"],
    }),
    "lane.b",
  );
}

/* Lane recency ranks by the lane's newest node, using the same clock as the
 * in-lane ordering: finished, else started, else created. */
function testLaneRecencyRanksByNewestNode(): void {
  const nodes = [
    node({ id: "n1", planspace_id: "lane.old", created_at: 10 }),
    node({ id: "n2", planspace_id: "lane.new", created_at: 5, finished_at: 90 }),
    node({ id: "n3", planspace_id: "lane.mid", created_at: 50 }),
    node({ id: "n4", planspace_id: "lane.new", created_at: 1 }),
  ];
  assert.deepEqual(
    lanesByRecentActivity(nodes, ["lane.old", "lane.new", "lane.mid"]),
    ["lane.new", "lane.mid", "lane.old"],
  );

  /* Lanes outside the allowed set are dropped, and an empty lane never
   * appears — "never used" is not a rank, and focus should fall through to
   * the stable first-visible tiebreak instead. */
  assert.deepEqual(lanesByRecentActivity(nodes, ["lane.mid"]), ["lane.mid"]);
  assert.deepEqual(lanesByRecentActivity(nodes, ["lane.empty"]), []);
  assert.deepEqual(lanesByRecentActivity([], ["lane.a"]), []);

  /* Nodes with no lane are ignored rather than grouped under a phantom. */
  assert.deepEqual(
    lanesByRecentActivity([node({ id: "x", created_at: 999 })], ["lane.a"]),
    [],
  );
}

/* Lane attribution must match what the canvas draws. A node predating the
 * `planspace_id` column is placed by its launch snapshot or by its parent, and
 * ranking that reads only the column reports those lanes as never used.
 *
 * The consequence is not cosmetic: with no stored focus and no active lane, a
 * project whose recent work is all legacy nodes would rank every lane as
 * unused, fall through to "first visible lane", and persist that — sending the
 * user somewhere other than where they last worked, permanently. */
function testLaneRecencyUsesTheCanvasLaneAttribution(): void {
  const nodes = [
    /* Legacy: no planspace_id, lane carried by the launch snapshot. */
    node({
      id: "legacy",
      created_at: 100,
      settings_snapshot: { active_planspace_id: "lane.legacy" },
    }),
    /* Legacy child: lane inherited from its parent. */
    node({ id: "child", created_at: 120, parent_node_id: "legacy" }),
    node({ id: "modern", planspace_id: "lane.modern", created_at: 50 }),
  ];
  const lanes = ["lane.legacy", "lane.modern"];

  /* The plain-column default cannot see the legacy lane at all. */
  assert.deepEqual(
    lanesByRecentActivity(nodes, lanes),
    ["lane.modern"],
    "reading only the column hides lanes whose nodes predate it",
  );

  /* With the canvas resolver, the legacy lane is both visible and correctly
   * ranked ahead of the older modern one. */
  assert.deepEqual(
    lanesByRecentActivity(nodes, lanes, nodeLaneResolver(nodes)),
    ["lane.legacy", "lane.modern"],
  );
}

/* The end-to-end shape App.tsx relies on: remember a lane, come back to the
 * project, land on it — and once it is gone, land somewhere usable instead
 * of nowhere. */
function testReturningToAProjectRestoresItsLane(): void {
  installStorage(memoryStorage());
  const nodes = [
    node({ id: "n1", planspace_id: "lane.a", created_at: 10 }),
    node({ id: "n2", planspace_id: "lane.b", created_at: 20 }),
  ];
  const visible = ["lane.a", "lane.b"];

  writeFocusedLane("proj", "lane.a");
  assert.equal(
    resolveFocusedLane({
      stored: readFocusedLane("proj"),
      active: null,
      visible,
      recentlyActive: lanesByRecentActivity(nodes, visible),
    }),
    "lane.a",
    "the remembered lane wins over the more recently active one",
  );

  /* lane.a is deleted: the remaining lane takes over rather than leaving the
   * canvas with no create target. */
  assert.equal(
    resolveFocusedLane({
      stored: readFocusedLane("proj"),
      active: null,
      visible: ["lane.b"],
      recentlyActive: lanesByRecentActivity(nodes, ["lane.b"]),
    }),
    "lane.b",
  );
}

testResolutionPrefersTheMostDeliberateSignal();
testResolutionSkipsLanesThatCannotBeSeen();
testStoredLanesAreScopedPerProject();
testCorruptRecordsDegradeToNoMemory();
testStorageFailuresAreContained();
testLaneRecencyRanksByNewestNode();
testLaneRecencyUsesTheCanvasLaneAttribution();
testReturningToAProjectRestoresItsLane();

delete (globalThis as { window?: unknown }).window;
console.log("focused lane tests passed");
