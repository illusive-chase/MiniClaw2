import assert from "node:assert/strict";

import {
  DISMISSED_UPDATE_STORAGE_KEY,
  SELF_UPDATE_POLL_MS,
  canApplyUpdate,
  readDismissedUpdate,
  targetSha,
  writeDismissedUpdate,
} from "../src/selfUpdate";
import type { SelfUpdateState } from "../src/types";

assert.equal(SELF_UPDATE_POLL_MS, 10 * 60_000);

function state(overrides: Partial<SelfUpdateState> = {}): SelfUpdateState {
  return {
    is_repo: true,
    available: true,
    fast_forward: true,
    dirty: false,
    head: "old",
    branch: "main",
    upstream: "origin/main",
    ahead: 0,
    behind: 1,
    commits: [{ sha: "new", title: "update", author: "dev", authored_at: 1 }],
    last_checked_at: 1,
    checking: false,
    error: null,
    blockers: [],
    ...overrides,
  };
}

assert.equal(targetSha(state()), "new");
assert.equal(targetSha(state({ available: false })), null);
assert.equal(canApplyUpdate(state()), true);
assert.equal(canApplyUpdate(state({ dirty: true })), false);
assert.equal(canApplyUpdate(state({ ahead: 1 })), false);
assert.equal(
  canApplyUpdate(state({ blockers: [{ project_id: "p", project_name: "P", node_id: "n", state: "running" }] })),
  false,
);

const values = new Map<string, string>();
Object.assign(globalThis, {
  window: {
    localStorage: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    },
  },
});
writeDismissedUpdate("abc");
assert.equal(values.get(DISMISSED_UPDATE_STORAGE_KEY), "abc");
assert.equal(readDismissedUpdate(), "abc");
