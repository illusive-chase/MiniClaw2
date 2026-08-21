import assert from "node:assert/strict";

import { canApplyUpdate } from "../src/selfUpdate";
import type { SelfUpdateState } from "../src/types";

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
    ref_at: 1,
    error: null,
    blockers: [],
    ...overrides,
  };
}

assert.equal(canApplyUpdate(state()), true);
assert.equal(canApplyUpdate(null), false);
assert.equal(canApplyUpdate(state({ available: false })), false);
assert.equal(canApplyUpdate(state({ fast_forward: false })), false);
assert.equal(canApplyUpdate(state({ dirty: true })), false);
assert.equal(canApplyUpdate(state({ ahead: 1 })), false);
assert.equal(
  canApplyUpdate(state({ blockers: [{ project_id: "p", project_name: "P", node_id: "n", state: "running" }] })),
  false,
);

console.log("self-update tests passed");
