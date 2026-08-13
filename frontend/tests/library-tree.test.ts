import assert from "node:assert/strict";
import { ancestorDirectoryPaths } from "../src/hierarchy";
import {
  DEFAULT_OPEN_SECTIONS,
  LIBRARY_TREE_STORAGE_KEY,
  defaultLibraryTreeState,
  normalizeLibraryTreeState,
  readLibraryTreeState,
  writeLibraryTreeState,
} from "../src/libraryTreeState";

const SKILL_NAMES = [
  "arxiv-search",
  "lark-base",
  "lark-vc",
  "lark-vc-agent",
  "lark-workflow-meeting-summary",
  "lark-workflow-standup-report",
  "ticktick-cli",
];

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
      assert.equal(key, LIBRARY_TREE_STORAGE_KEY);
      return this.value;
    },
    setItem(key, value) {
      assert.equal(key, LIBRARY_TREE_STORAGE_KEY);
      this.value = value;
    },
  };
}

/* Folding is what makes this non-trivial: `lark-workflow-meeting-summary` lives
 * under `lark` › `workflow`, and no `lark-workflow-meeting` directory exists, so
 * splitting the slug on `-` would produce keys that never match the tree. */
function testAncestorPathsFollowFoldedTree(): void {
  assert.deepEqual(
    ancestorDirectoryPaths(SKILL_NAMES, "lark-workflow-meeting-summary"),
    ["lark", "lark-workflow"],
  );
  assert.deepEqual(ancestorDirectoryPaths(SKILL_NAMES, "lark-base"), ["lark"]);

  /* `lark-vc` is both a directory and an entry: reaching its own row needs
   * `lark` and `lark-vc` open, since the row renders inside the expansion. */
  assert.deepEqual(ancestorDirectoryPaths(SKILL_NAMES, "lark-vc"), [
    "lark",
    "lark-vc",
  ]);
  assert.deepEqual(ancestorDirectoryPaths(SKILL_NAMES, "lark-vc-agent"), [
    "lark",
    "lark-vc",
  ]);

  /* Collapsed single-child roots are top-level rows, so nothing to expand. */
  assert.deepEqual(ancestorDirectoryPaths(SKILL_NAMES, "arxiv-search"), []);
  assert.deepEqual(ancestorDirectoryPaths(SKILL_NAMES, "ticktick-cli"), []);

  assert.deepEqual(ancestorDirectoryPaths(SKILL_NAMES, "not-present-at-all"), []);
  assert.deepEqual(ancestorDirectoryPaths([], "lark-base"), []);
}

function testDefaultsSurviveMissingAndCorruptRecords(): void {
  assert.deepEqual(defaultLibraryTreeState(), {
    open: { ...DEFAULT_OPEN_SECTIONS },
    expanded: { templates: [], principles: [], skills: [] },
  });

  /* A truncated or hand-edited record must not silently flip a section closed
   * that the shipped default has open. */
  for (const corrupt of [null, 42, "nope", [], {}, { open: null }, { open: [] }]) {
    assert.deepEqual(
      normalizeLibraryTreeState(corrupt).open,
      DEFAULT_OPEN_SECTIONS,
      `unexpected open state for ${JSON.stringify(corrupt)}`,
    );
  }

  const partial = normalizeLibraryTreeState({
    open: { skills: true, bogus: true, principles: "yes" },
    expanded: { skills: ["lark", "lark", 7, "", "lark-vc"], bogus: ["x"] },
  });
  assert.deepEqual(partial.open, {
    templates: true,
    principles: false,
    skills: true,
  });
  assert.deepEqual(partial.expanded.skills, ["lark", "lark-vc"]);
  assert.deepEqual(partial.expanded.templates, []);
  assert.deepEqual(partial.expanded.principles, []);
  assert.equal("bogus" in partial.open, false);
  assert.equal("bogus" in partial.expanded, false);
}

function testRoundTripThroughStorage(): void {
  const storage = memoryStorage();
  installStorage(storage);
  assert.deepEqual(readLibraryTreeState(), defaultLibraryTreeState());

  writeLibraryTreeState({
    open: { templates: false, principles: true, skills: true },
    expanded: { templates: ["gui"], principles: [], skills: ["lark", "lark-vc"] },
  });
  assert.deepEqual(readLibraryTreeState(), {
    open: { templates: false, principles: true, skills: true },
    expanded: { templates: ["gui"], principles: [], skills: ["lark", "lark-vc"] },
  });

  installStorage(memoryStorage("{not json"));
  assert.deepEqual(readLibraryTreeState(), defaultLibraryTreeState());
}

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
  assert.doesNotThrow(() => readLibraryTreeState());
  assert.deepEqual(readLibraryTreeState(), defaultLibraryTreeState());
  assert.doesNotThrow(() => writeLibraryTreeState(defaultLibraryTreeState()));
}

testAncestorPathsFollowFoldedTree();
testDefaultsSurviveMissingAndCorruptRecords();
testRoundTripThroughStorage();
testStorageFailuresAreContained();

delete (globalThis as { window?: unknown }).window;
console.log("library tree tests passed");
