import assert from "node:assert/strict";
import type { SessionInfo, Tag } from "../src/types";
import {
  RECENT_HIDDEN_AT_OR_BELOW,
  RECENT_PROJECT_COUNT,
  UNTAGGED_GROUP_ID,
  activityAt,
  filterByBinding,
  filterByTags,
  groupByTag,
  recentProjects,
  resolveTags,
  sortFlat,
  tagCounts,
} from "../src/projectGrouping";
import {
  DEFAULT_PROJECT_SORT,
  PROJECT_SORT_STORAGE_KEY,
  normalizeProjectSort,
  readProjectSort,
  writeProjectSort,
} from "../src/projectSort";
import { TAG_COLORS, defaultColorForName, isTagColor } from "../src/tagPalette";

function tag(id: string, name: string, color = "coral"): Tag {
  return { id, name, color, created_at: 0 };
}

function project(
  id: string,
  opts: {
    name?: string;
    created_at?: number;
    last_activity_at?: number | null;
    tag_ids?: string[];
    bound_here?: boolean;
  } = {},
): SessionInfo {
  return {
    id,
    created_at: opts.created_at ?? 1000,
    turns: 0,
    model_preset_id: "p",
    concurrency: 1,
    active_count: 0,
    queued_count: 0,
    name: opts.name,
    tag_ids: opts.tag_ids,
    last_activity_at: opts.last_activity_at,
    machine_id: "m",
    local_machine_id: "m",
    created_on_machine_label: "here",
    bound_here: opts.bound_here ?? true,
    read_only: false,
    can_delete: true,
    can_bind_here: false,
    hosts: [],
  };
}

const WORK = tag("t_work", "work", "coral");
const RESEARCH = tag("t_res", "research", "sage");
const INFRA = tag("t_infra", "infra", "azure");
const TAGS = [WORK, RESEARCH, INFRA];

/* `last_activity_at` is derived server-side and can be absent entirely, so every
 * ordering path has to stay comparable via `created_at`. */
function testActivityFallsBackToCreatedAt(): void {
  assert.equal(activityAt(project("a", { created_at: 500 })), 500);
  assert.equal(
    activityAt(project("a", { created_at: 500, last_activity_at: 900 })),
    900,
  );
  assert.equal(
    activityAt(project("a", { created_at: 500, last_activity_at: null })),
    500,
  );
  /* An older activity stamp than creation is not second-guessed: the backend
   * owns that value and clamping here would hide a real backend bug. */
  assert.equal(
    activityAt(project("a", { created_at: 500, last_activity_at: 100 })),
    100,
  );
}

function testRecentIsGlobalNewestFirst(): void {
  const sessions = [
    project("a", { last_activity_at: 300 }),
    project("b", { last_activity_at: 900 }),
    project("c", { created_at: 700 }),
    project("d", { last_activity_at: 500 }),
    project("e", { last_activity_at: 100 }),
  ];
  assert.deepEqual(
    recentProjects(sessions).map((s) => s.id),
    ["b", "c", "d"],
  );
  assert.equal(recentProjects(sessions).length, RECENT_PROJECT_COUNT);

  /* Hidden below the threshold: with four or fewer projects the strip would
   * restate most of the list directly above it. */
  assert.deepEqual(recentProjects(sessions.slice(0, 4)), []);
  assert.equal(RECENT_HIDDEN_AT_OR_BELOW, 4);
  assert.deepEqual(recentProjects([]), []);

  /* Equal timestamps must not reshuffle between 10s refreshes. */
  const tied = [
    project("z", { last_activity_at: 100 }),
    project("y", { last_activity_at: 100 }),
    project("x", { last_activity_at: 100 }),
    project("w", { last_activity_at: 100 }),
    project("v", { last_activity_at: 100 }),
  ];
  assert.deepEqual(recentProjects(tied).map((s) => s.id), ["v", "w", "x"]);
}

/* The recent strip is computed from the unfiltered list on purpose: it is a
 * stable shortcut, and going empty under a filter would defeat that. */
function testRecentIgnoresTagFilter(): void {
  const sessions = [
    project("a", { last_activity_at: 900, tag_ids: [] }),
    project("b", { last_activity_at: 800, tag_ids: [WORK.id] }),
    project("c", { last_activity_at: 700, tag_ids: [] }),
    project("d", { last_activity_at: 600, tag_ids: [] }),
    project("e", { last_activity_at: 500, tag_ids: [] }),
  ];
  const filtered = filterByTags(sessions, new Set([WORK.id]));
  assert.deepEqual(filtered.map((s) => s.id), ["b"]);
  assert.deepEqual(recentProjects(sessions).map((s) => s.id), ["a", "b", "c"]);
}

function testFilterIsMultiSelectAnd(): void {
  const sessions = [
    project("both", { tag_ids: [WORK.id, RESEARCH.id] }),
    project("workOnly", { tag_ids: [WORK.id] }),
    project("resOnly", { tag_ids: [RESEARCH.id] }),
    project("none", {}),
    project("all3", { tag_ids: [WORK.id, RESEARCH.id, INFRA.id] }),
  ];
  assert.deepEqual(filterByTags(sessions, new Set()).length, 5);
  assert.deepEqual(
    filterByTags(sessions, new Set([WORK.id])).map((s) => s.id),
    ["both", "workOnly", "all3"],
  );
  /* AND, not OR: work+research keeps only projects carrying both. */
  assert.deepEqual(
    filterByTags(sessions, new Set([WORK.id, RESEARCH.id])).map((s) => s.id),
    ["both", "all3"],
  );
  assert.deepEqual(
    filterByTags(sessions, new Set([WORK.id, RESEARCH.id, INFRA.id])).map((s) => s.id),
    ["all3"],
  );
  /* Empty result is a real state the UI has to render, not an error. */
  assert.deepEqual(filterByTags([project("none", {})], new Set([WORK.id])), []);
}

function testBindingFilterIsOrthogonal(): void {
  const bound = project("bound", { bound_here: true, tag_ids: [WORK.id] });
  const unbound = project("unbound", { bound_here: false, tag_ids: [WORK.id] });
  assert.deepEqual(filterByBinding([bound, unbound], "all"), [bound, unbound]);
  assert.deepEqual(filterByBinding([bound, unbound], "bound"), [bound]);
  assert.deepEqual(filterByBinding([bound, unbound], "unbound"), [unbound]);
  assert.deepEqual(filterByTags(filterByBinding([bound, unbound], "bound"), new Set([WORK.id])), [bound]);
}

/* Counts are per-tag over all projects, so they stay meaningful while a filter
 * is active instead of collapsing to 0/1. */
function testTagCountsIgnoreFilter(): void {
  const counts = tagCounts([
    project("a", { tag_ids: [WORK.id, RESEARCH.id] }),
    project("b", { tag_ids: [WORK.id] }),
    project("c", {}),
    project("d", { tag_ids: ["t_gone"] }),
  ]);
  assert.equal(counts.get(WORK.id), 2);
  assert.equal(counts.get(RESEARCH.id), 1);
  assert.equal(counts.get(INFRA.id), undefined);
  assert.equal(counts.get("t_gone"), 1);
}

function testGroupsFollowTagOrderAndRepeatMembers(): void {
  const sessions = [
    project("multi", { tag_ids: [INFRA.id, WORK.id], last_activity_at: 900 }),
    project("work", { tag_ids: [WORK.id], last_activity_at: 800 }),
    project("bare", {}),
  ];
  const groups = groupByTag(sessions, TAGS);
  /* Section order follows tags.json, not the projects' own tag order — `multi`
   * lists infra first but still appears under work first. */
  assert.deepEqual(groups.map((g) => g.id), [WORK.id, INFRA.id, UNTAGGED_GROUP_ID]);
  /* A multi-tag project appears in each of its sections (design §1.6). */
  assert.deepEqual(groups[0].sessions.map((s) => s.id), ["multi", "work"]);
  assert.deepEqual(groups[1].sessions.map((s) => s.id), ["multi"]);
  assert.deepEqual(groups[2].sessions.map((s) => s.id), ["bare"]);
  assert.equal(groups[2].label, "未分类");
  assert.equal(groups[2].color, "neutral");
  /* research has no members, so it contributes no bare header. */
  assert.equal(groups.some((g) => g.id === RESEARCH.id), false);

  assert.deepEqual(groupByTag([], TAGS), []);
  /* No tags loaded yet: everything is one untagged bucket rather than nothing. */
  const noTags = groupByTag(sessions, []);
  assert.deepEqual(noTags.map((g) => g.id), [UNTAGGED_GROUP_ID]);
  assert.equal(noTags[0].sessions.length, 3);
}

/* A tag deleted on another machine leaves a dangling id in project.json. It must
 * not put the project in "未分类" while /tags is still resolving, but once the
 * id is genuinely unknown the project does belong there. */
function testUnknownTagIdsFallIntoUntagged(): void {
  const groups = groupByTag([project("ghost", { tag_ids: ["t_gone"] })], TAGS);
  assert.deepEqual(groups.map((g) => g.id), [UNTAGGED_GROUP_ID]);

  const mixed = groupByTag([project("mixed", { tag_ids: ["t_gone", WORK.id] })], TAGS);
  assert.deepEqual(mixed.map((g) => g.id), [WORK.id]);
}

function testFlatSorts(): void {
  const sessions = [
    project("b", { name: "beta", last_activity_at: 100 }),
    project("a", { name: "Alpha", last_activity_at: 900 }),
    project("c", { name: "", created_at: 500 }),
    project("d", { name: "  ", last_activity_at: 700 }),
  ];
  /* Case-insensitive, and unnamed projects sort last — a run of "(unnamed)" at
   * the top of an alphabetical list carries no information. */
  assert.deepEqual(sortFlat(sessions, "name").map((s) => s.id), ["a", "b", "c", "d"]);
  assert.deepEqual(
    sortFlat(sessions, "activity").map((s) => s.id),
    ["a", "d", "c", "b"],
  );
  /* Input is not mutated: the caller keeps the server's list order. */
  assert.deepEqual(sessions.map((s) => s.id), ["b", "a", "c", "d"]);
}

function testResolveTagsFollowsTagOrder(): void {
  const byId = new Map(TAGS.map((t) => [t.id, t]));
  assert.deepEqual(
    resolveTags(project("a", { tag_ids: [INFRA.id, WORK.id] }), byId).map((t) => t.name),
    ["work", "infra"],
  );
  assert.deepEqual(resolveTags(project("a", {}), byId), []);
  assert.deepEqual(resolveTags(project("a", { tag_ids: ["t_gone"] }), byId), []);
}

function testDefaultColorIsStableAndInPalette(): void {
  for (const name of ["work", "research", "urgent", "紧急", "a", "", "  Work  "]) {
    const color = defaultColorForName(name);
    assert.ok(isTagColor(color), `${name} -> ${color} not in palette`);
    assert.equal(defaultColorForName(name), color, "must be deterministic");
  }
  /* Case and surrounding whitespace must not change the color, so a user who
   * retypes a name sees the same one. */
  assert.equal(defaultColorForName("Work"), defaultColorForName("work"));
  assert.equal(defaultColorForName("  work "), defaultColorForName("work"));
  assert.equal(TAG_COLORS.length, 8);

  /* Spread across the palette rather than collapsing onto one key. */
  const names = Array.from({ length: 200 }, (_, i) => `tag-${i}`);
  const used = new Set(names.map(defaultColorForName));
  assert.equal(used.size, TAG_COLORS.length, `only hit ${used.size} of 8 colors`);
}

type FakeStorage = {
  value: string | null;
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
};

function installStorage(storage: FakeStorage | null): void {
  Object.defineProperty(globalThis, "window", {
    value: storage ? { localStorage: storage } : {},
    configurable: true,
    writable: true,
  });
}

function memoryStorage(initial: string | null = null): FakeStorage {
  return {
    value: initial,
    getItem(key) {
      assert.equal(key, PROJECT_SORT_STORAGE_KEY);
      return this.value;
    },
    setItem(key, value) {
      assert.equal(key, PROJECT_SORT_STORAGE_KEY);
      this.value = value;
    },
  };
}

function testSortModePersistence(): void {
  assert.equal(DEFAULT_PROJECT_SORT, "grouped");
  for (const bad of [null, "", "nope", 7, {}, []]) {
    assert.equal(normalizeProjectSort(bad), DEFAULT_PROJECT_SORT);
  }
  assert.equal(normalizeProjectSort("name"), "name");
  assert.equal(normalizeProjectSort("activity"), "activity");

  const storage = memoryStorage();
  installStorage(storage);
  assert.equal(readProjectSort(), DEFAULT_PROJECT_SORT);
  writeProjectSort("activity");
  assert.equal(storage.value, "activity");
  assert.equal(readProjectSort(), "activity");

  /* A hand-edited value falls back rather than rendering an unknown mode. */
  storage.value = "garbage";
  assert.equal(readProjectSort(), DEFAULT_PROJECT_SORT);

  /* localStorage unavailable (private window, storage disabled) must not throw:
   * the landing page still has to render. */
  installStorage(null);
  assert.equal(readProjectSort(), DEFAULT_PROJECT_SORT);
  writeProjectSort("name");
}

testActivityFallsBackToCreatedAt();
testRecentIsGlobalNewestFirst();
testRecentIgnoresTagFilter();
testFilterIsMultiSelectAnd();
testBindingFilterIsOrthogonal();
testTagCountsIgnoreFilter();
testGroupsFollowTagOrderAndRepeatMembers();
testUnknownTagIdsFallIntoUntagged();
testFlatSorts();
testResolveTagsFollowsTagOrder();
testDefaultColorIsStableAndInPalette();
testSortModePersistence();

console.log("project-tags tests passed");
