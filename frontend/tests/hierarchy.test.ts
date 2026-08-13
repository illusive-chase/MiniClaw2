import assert from "node:assert/strict";
import {
  buildHierarchy,
  searchEntries,
  type HierarchyNode,
} from "../src/hierarchy";
import {
  RECENT_USED_STORAGE_KEY,
  readRecentUsed,
  recordRecentUsed,
  writeRecentUsed,
} from "../src/recentUsed";

const SKILL_NAMES = [
  "arxiv-search",
  "lark-approval",
  "lark-apps",
  "lark-attendance",
  "lark-base",
  "lark-calendar",
  "lark-contact",
  "lark-doc",
  "lark-drive",
  "lark-event",
  "lark-im",
  "lark-mail",
  "lark-markdown",
  "lark-minutes",
  "lark-note",
  "lark-okr",
  "lark-openapi-explorer",
  "lark-shared",
  "lark-sheets",
  "lark-skill-maker",
  "lark-slides",
  "lark-task",
  "lark-vc",
  "lark-vc-agent",
  "lark-whiteboard",
  "lark-wiki",
  "lark-workflow-meeting-summary",
  "lark-workflow-standup-report",
  "ticktick-cli",
];

function leaf(entry: string, segment: string): HierarchyNode {
  return {
    segment,
    fullPath: entry.split("-"),
    entry,
    children: [],
    leafCount: 1,
  };
}

function larkLeaf(segment: string): HierarchyNode {
  return leaf(`lark-${segment}`, segment);
}

function testRealSkillHierarchyGoldenCase(): void {
  const expected: HierarchyNode[] = [
    leaf("arxiv-search", "arxiv-search"),
    {
      segment: "lark",
      fullPath: ["lark"],
      entry: null,
      children: [
        larkLeaf("approval"),
        larkLeaf("apps"),
        larkLeaf("attendance"),
        larkLeaf("base"),
        larkLeaf("calendar"),
        larkLeaf("contact"),
        larkLeaf("doc"),
        larkLeaf("drive"),
        larkLeaf("event"),
        larkLeaf("im"),
        larkLeaf("mail"),
        larkLeaf("markdown"),
        larkLeaf("minutes"),
        larkLeaf("note"),
        larkLeaf("okr"),
        larkLeaf("openapi-explorer"),
        larkLeaf("shared"),
        larkLeaf("sheets"),
        larkLeaf("skill-maker"),
        larkLeaf("slides"),
        larkLeaf("task"),
        {
          segment: "vc",
          fullPath: ["lark", "vc"],
          entry: "lark-vc",
          children: [leaf("lark-vc-agent", "agent")],
          leafCount: 2,
        },
        larkLeaf("whiteboard"),
        larkLeaf("wiki"),
        {
          segment: "workflow",
          fullPath: ["lark", "workflow"],
          entry: null,
          children: [
            leaf("lark-workflow-meeting-summary", "meeting-summary"),
            leaf("lark-workflow-standup-report", "standup-report"),
          ],
          leafCount: 2,
        },
      ],
      leafCount: 27,
    },
    leaf("ticktick-cli", "ticktick-cli"),
  ];

  assert.deepEqual(buildHierarchy(SKILL_NAMES), expected);
}

function testDirectoryCanAlsoBeAnEntry(): void {
  const [lark] = buildHierarchy(["lark-vc-agent", "lark-base", "lark-vc"]);
  assert.equal(lark.segment, "lark");
  const vc = lark.children.find((child) => child.segment === "vc");
  assert.deepEqual(vc, {
    segment: "vc",
    fullPath: ["lark", "vc"],
    entry: "lark-vc",
    children: [leaf("lark-vc-agent", "agent")],
    leafCount: 2,
  });
}

function testSingleChildDirectoriesCollapse(): void {
  assert.deepEqual(buildHierarchy(["ticktick-cli", "arxiv-search"]), [
    leaf("arxiv-search", "arxiv-search"),
    leaf("ticktick-cli", "ticktick-cli"),
  ]);
}

function testDuplicateEntriesDoNotInflateCounts(): void {
  assert.deepEqual(buildHierarchy(["alpha-one", "alpha-one"]), [
    leaf("alpha-one", "alpha-one"),
  ]);
}

function testSearchRankingAndRanges(): void {
  assert.deepEqual(
    searchEntries([
      "network-helper",
      "workflow-zeta",
      "lark-workflow",
      "beta-workbench",
      "workflow-alpha",
    ], "WORK"),
    [
      {
        entry: "workflow-alpha",
        fullPath: ["workflow", "alpha"],
        matchRange: [0, 4],
      },
      {
        entry: "workflow-zeta",
        fullPath: ["workflow", "zeta"],
        matchRange: [0, 4],
      },
      {
        entry: "beta-workbench",
        fullPath: ["beta", "workbench"],
        matchRange: [5, 9],
      },
      {
        entry: "lark-workflow",
        fullPath: ["lark", "workflow"],
        matchRange: [5, 9],
      },
      {
        entry: "network-helper",
        fullPath: ["network", "helper"],
        matchRange: [3, 7],
      },
    ],
  );
  assert.deepEqual(searchEntries(["lark-vc-agent"], "vc-a"), [{
    entry: "lark-vc-agent",
    fullPath: ["lark", "vc", "agent"],
    matchRange: [5, 9],
  }]);
  assert.deepEqual(searchEntries(SKILL_NAMES, "   "), []);
}

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
      assert.equal(key, RECENT_USED_STORAGE_KEY);
      return this.value;
    },
    setItem(key, value) {
      assert.equal(key, RECENT_USED_STORAGE_KEY);
      this.value = value;
    },
  };
}

function testRecentUsedMruAndLimit(): void {
  const storage = memoryStorage();
  installStorage(storage);
  assert.deepEqual(readRecentUsed(), { skill: [], principle: [] });

  for (let index = 0; index < 10; index += 1) {
    recordRecentUsed("skill", `skills.skill-${index}`);
  }
  recordRecentUsed("principle", "principles.careful");
  recordRecentUsed("skill", "skills.skill-4");

  assert.deepEqual(readRecentUsed(), {
    skill: [
      "skills.skill-4",
      "skills.skill-9",
      "skills.skill-8",
      "skills.skill-7",
      "skills.skill-6",
      "skills.skill-5",
      "skills.skill-3",
      "skills.skill-2",
    ],
    principle: ["principles.careful"],
  });
  assert.deepEqual(JSON.parse(storage.value ?? ""), readRecentUsed());
}

function testRecentUsedCleansStoredData(): void {
  const storage = memoryStorage(JSON.stringify({
    skill: ["skills.a", 42, "skills.a", "", "skills.b"],
    principle: "not-an-array",
    ignored: ["other"],
  }));
  installStorage(storage);
  assert.deepEqual(readRecentUsed(), {
    skill: ["skills.a", "skills.b"],
    principle: [],
  });

  writeRecentUsed({
    skill: ["skills.a", "skills.a", "skills.b"],
    principle: ["principles.one"],
  });
  assert.deepEqual(JSON.parse(storage.value ?? ""), {
    skill: ["skills.a", "skills.b"],
    principle: ["principles.one"],
  });
}

function testRecentUsedContainsStorageFailures(): void {
  installStorage({
    value: null,
    getItem() {
      throw new Error("storage unavailable");
    },
    setItem() {
      throw new Error("storage unavailable");
    },
  });
  assert.doesNotThrow(() => readRecentUsed());
  assert.deepEqual(readRecentUsed(), { skill: [], principle: [] });
  assert.doesNotThrow(() => recordRecentUsed("skill", "skills.lark-base"));
  assert.doesNotThrow(() => writeRecentUsed({ skill: [], principle: [] }));
}

testRealSkillHierarchyGoldenCase();
testDirectoryCanAlsoBeAnEntry();
testSingleChildDirectoriesCollapse();
testDuplicateEntriesDoNotInflateCounts();
testSearchRankingAndRanges();
testRecentUsedMruAndLimit();
testRecentUsedCleansStoredData();
testRecentUsedContainsStorageFailures();

delete (globalThis as { window?: unknown }).window;
console.log("hierarchy tests passed");
