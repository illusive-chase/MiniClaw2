import assert from "node:assert/strict";
import type { HomeItem } from "../src/types";
import {
  NEW_FOLDER_NAME,
  UNNAMED_FOLDER,
  commitFolderName,
  dissolve,
  folderBadges,
  folderOf,
  mergeInto,
  moveInFolder,
  moveSlot,
  moveToFolder,
  normalizeForSave,
  projectIds,
  pullOut,
  reconcile,
  renameFolder,
  slotKey,
  wrapInNewFolder,
} from "../src/homeLayout";

const p = (project: string): HomeItem => ({ project });
const f = (id: string, name: string, ...projects: string[]): HomeItem => ({ folder: { id, name, projects } });

// slot keys and lookups
assert.equal(slotKey(p("a")), "p:a");
assert.equal(slotKey(f("g_1", "x", "a")), "f:g_1");
assert.deepEqual(projectIds([p("a"), f("g_1", "x", "b", "c"), p("d")]), ["a", "b", "c", "d"]);
assert.equal(folderOf([p("a"), f("g_1", "x", "b")], "b")?.id, "g_1");
assert.equal(folderOf([p("a")], "a"), null);

// reorder
{
  const items = [p("a"), p("b"), f("g_1", "x", "c"), p("d")];
  assert.deepEqual(moveSlot(items, "p:a", 2).map(slotKey), ["p:b", "f:g_1", "p:a", "p:d"]);
  assert.deepEqual(moveSlot(items, "f:g_1", 0).map(slotKey), ["f:g_1", "p:a", "p:b", "p:d"]);
  assert.deepEqual(moveSlot(items, "p:d", 99).map(slotKey), items.map(slotKey));
  assert.equal(moveSlot(items, "p:a", 0), items, "a no-op keeps identity");
  const inside = moveInFolder([f("g_1", "x", "a", "b", "c")], "g_1", "a", 2);
  assert.deepEqual(inside, [f("g_1", "x", "b", "c", "a")]);
}

// merging creates a folder in the target's place, or appends to one
{
  const created = mergeInto([p("a"), p("b"), p("c")], "p:c", "a", "g_new");
  assert.ok(created);
  assert.equal(created.created, true);
  assert.deepEqual(created.items, [p("b"), f("g_new", NEW_FOLDER_NAME, "c", "a")]);

  const appended = mergeInto([p("a"), f("g_1", "Web", "b"), p("c")], "f:g_1", "c");
  assert.ok(appended);
  assert.equal(appended.created, false);
  assert.equal(appended.folderId, "g_1");
  assert.deepEqual(appended.items, [p("a"), f("g_1", "Web", "b", "c")]);

  assert.equal(mergeInto([p("a")], "p:a", "a"), null, "cannot merge onto itself");
  assert.equal(mergeInto([p("a")], "p:zz", "a"), null, "unknown target");
}

// pulling out places the project after its folder; the last one out removes it
{
  const items = [p("a"), f("g_1", "x", "b", "c"), p("d")];
  assert.deepEqual(pullOut(items, "b"), [p("a"), f("g_1", "x", "c"), p("b"), p("d")]);
  assert.deepEqual(pullOut([f("g_1", "x", "b")], "b"), [p("b")]);
}

// dissolving returns members in order to the folder's place
assert.deepEqual(
  dissolve([p("a"), f("g_1", "x", "c", "b"), p("d")], "g_1"),
  [p("a"), p("c"), p("b"), p("d")],
);

// sidebar "所在组"
{
  const items = [p("a"), f("g_1", "x", "b"), f("g_2", "y", "c")];
  assert.deepEqual(moveToFolder(items, "a", "g_2"), [f("g_1", "x", "b"), f("g_2", "y", "c", "a")]);
  // Leaving g_1 empties it, so it disappears.
  assert.deepEqual(moveToFolder(items, "b", "g_2"), [p("a"), f("g_2", "y", "c", "b")]);
  assert.equal(moveToFolder(items, "b", "g_1"), items);
  assert.deepEqual(wrapInNewFolder(items, "a", "g_n"), [f("g_n", NEW_FOLDER_NAME, "a"), f("g_1", "x", "b"), f("g_2", "y", "c")]);
}

// folder names
assert.deepEqual(renameFolder([f("g_1", "x", "a")], "g_1", "  draft "), [f("g_1", "  draft ", "a")]);
assert.equal(commitFolderName("  模型研究  "), "模型研究");
assert.equal(commitFolderName("   "), UNNAMED_FOLDER);
assert.equal(commitFolderName("x".repeat(60)).length, 40);
assert.deepEqual(normalizeForSave([f("g_1", " ", "a"), p("b")]), [f("g_1", UNNAMED_FOLDER, "a"), p("b")]);

// reconcile mirrors the server: prune, then append in the given order
assert.deepEqual(
  reconcile([p("gone"), f("g_1", "x", "gone2"), f("g_2", "y", "a", "gone3"), p("b")], ["c", "a", "b"]),
  [f("g_2", "y", "a"), p("b"), p("c")],
);

// folder badges sum their members
{
  const byProject = new Map([
    ["a", { waiting: 2, errors: 0, running: 1 }],
    ["b", { waiting: 1, errors: 1, running: 0 }],
  ]);
  const folder = { id: "g_1", name: "x", projects: ["a", "b", "c"] };
  assert.deepEqual(folderBadges(folder, byProject), { waiting: 3, errors: 1, running: 1 });
}

console.log("主屏排列纯函数测试通过：重排、建组、移出、解散、所在组、组名、对账与角标汇总");
