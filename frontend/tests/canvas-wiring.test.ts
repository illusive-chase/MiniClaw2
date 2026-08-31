import assert from "node:assert/strict";

import {
  canConnectDependency,
  canDisconnectDependency,
  dependencyConnectionRejection,
  dependencyEdgeId,
  resolveWiringDrop,
  ScheduledDepsUpdateQueue,
  scheduledDepsAfterConnect,
  scheduledDepsAfterDisconnect,
} from "../src/canvas/dependencyWiring";
import { stackTop } from "../src/canvas/nodes/AgentNode";
import type { NodeInfo } from "../src/types";

function node(over: Partial<NodeInfo> = {}): NodeInfo {
  return {
    id: "n1",
    project_id: "p1",
    kind: "agent",
    state: "virtual",
    provider: "claude",
    model_preset_id: "gpt-5.5",
    prompt: "",
    prompt_draft: "do the thing",
    category: "regular",
    planspace_id: "lane-a",
    created_at: 0,
    ...over,
  } as NodeInfo;
}

/* Whole-array PATCHes for one target are serialized. The second rewrite sees
 * the first response, while a different target can write immediately. */
{
  const queue = new ScheduledDepsUpdateQueue();
  let releaseFirst: (() => void) | null = null;
  const firstBlocked = new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });
  const writes: Array<{ targetId: string; deps: string[] }> = [];
  const initial = new Map([
    ["dst", node({ id: "dst", scheduled_deps: [] })],
    ["other", node({ id: "other", scheduled_deps: [] })],
  ]);

  const enqueue = (targetId: string, sourceId: string) =>
    queue.enqueue(
      targetId,
      (current) => [...current, sourceId],
      {
        getTarget: () => initial.get(targetId),
        canMutate: () => true,
        write: async (deps) => {
          writes.push({ targetId, deps });
          if (targetId === "dst" && deps.length === 1) await firstBlocked;
          return node({ id: targetId, scheduled_deps: deps });
        },
      },
    );

  const first = enqueue("dst", "a");
  const second = enqueue("dst", "b");
  const parallel = enqueue("other", "x");
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(writes, [
    { targetId: "dst", deps: ["a"] },
    { targetId: "other", deps: ["x"] },
  ]);
  releaseFirst?.();
  await Promise.all([first, second, parallel]);
  assert.deepEqual(writes, [
    { targetId: "dst", deps: ["a"] },
    { targetId: "other", deps: ["x"] },
    { targetId: "dst", deps: ["a", "b"] },
  ]);

  let deniedWrites = 0;
  await queue.enqueue("denied", (current) => [...current, "a"], {
    getTarget: () => node({ id: "denied" }),
    canMutate: () => false,
    write: async () => {
      deniedWrites += 1;
      return undefined;
    },
  });
  assert.equal(deniedWrites, 0);
}

function graph(...nodes: NodeInfo[]): Map<string, NodeInfo> {
  return new Map(nodes.map((n) => [n.id, n]));
}

/* The ordinary case: one virtual may depend on an executed node in its lane. */
{
  const nodes = graph(
    node({ id: "src", state: "done" }),
    node({ id: "dst", state: "virtual" }),
  );
  assert.equal(
    dependencyConnectionRejection({ sourceId: "src", targetId: "dst" }, nodes),
    null,
  );
  assert.equal(
    canConnectDependency({ sourceId: "src", targetId: "dst" }, nodes),
    true,
  );
}

/* Each rejection mirrors a backend gate, so an illegal drop refuses to land
 * instead of snapping into place and then coming back as a 400. */
{
  const cases: Array<{
    why: string;
    attempt: Parameters<typeof dependencyConnectionRejection>[0];
    nodes: Map<string, NodeInfo>;
    expect: string;
  }> = [
    {
      why: "no endpoint",
      attempt: { sourceId: null, targetId: "dst" },
      nodes: graph(node({ id: "dst" })),
      expect: "missing-endpoint",
    },
    {
      why: "endpoint not in the graph",
      attempt: { sourceId: "ghost", targetId: "dst" },
      nodes: graph(node({ id: "dst" })),
      expect: "missing-endpoint",
    },
    {
      why: "a node cannot depend on itself",
      attempt: { sourceId: "dst", targetId: "dst" },
      nodes: graph(node({ id: "dst" })),
      expect: "self-reference",
    },
    {
      why: "only an agent holds scheduled_deps",
      attempt: { sourceId: "src", targetId: "dst" },
      nodes: graph(node({ id: "src" }), node({ id: "dst", kind: "op" })),
      expect: "target-not-agent",
    },
    {
      why: "a verifier holds no dependencies either",
      attempt: { sourceId: "src", targetId: "dst" },
      nodes: graph(node({ id: "src" }), node({ id: "dst", kind: "verifier" })),
      expect: "target-not-agent",
    },
    {
      why: "an executed graph is history",
      attempt: { sourceId: "src", targetId: "dst" },
      nodes: graph(node({ id: "src" }), node({ id: "dst", state: "done" })),
      expect: "target-not-virtual",
    },
    {
      why: "a queued node is past editing",
      attempt: { sourceId: "src", targetId: "dst" },
      nodes: graph(node({ id: "src" }), node({ id: "dst", state: "queued" })),
      expect: "target-not-virtual",
    },
    {
      why: "an obsolete target is not being planned any more",
      attempt: { sourceId: "src", targetId: "dst" },
      nodes: graph(
        node({ id: "src" }),
        node({ id: "dst", obsolete_reason: "superseded" }),
      ),
      expect: "target-obsolete",
    },
    {
      why: "cold starts cannot receive injected dependency context",
      attempt: { sourceId: "src", targetId: "dst" },
      nodes: graph(
        node({ id: "src" }),
        node({ id: "dst", agent_op_kind: "cold_start" }),
      ),
      expect: "target-cold-start",
    },
    {
      why: "dependencies stay within one lane",
      attempt: { sourceId: "src", targetId: "dst" },
      nodes: graph(
        node({ id: "src", planspace_id: "lane-b" }),
        node({ id: "dst", planspace_id: "lane-a" }),
      ),
      expect: "cross-lane",
    },
    {
      why: "the dependency already exists",
      attempt: { sourceId: "src", targetId: "dst" },
      nodes: graph(
        node({ id: "src" }),
        node({ id: "dst", scheduled_deps: ["src"] }),
      ),
      expect: "already-declared",
    },
  ];

  for (const { why, attempt, nodes, expect } of cases) {
    assert.equal(
      dependencyConnectionRejection(attempt, nodes),
      expect,
      `expected ${expect} because ${why}`,
    );
  }
}

/* Cycles are refused before the drop, matching the backend's post-update check.
 * Reachability, not just the direct pair: a→b→c means c→a closes a loop. */
{
  const nodes = graph(
    node({ id: "a" }),
    node({ id: "b", scheduled_deps: ["a"] }),
    node({ id: "c", scheduled_deps: ["b"] }),
  );
  assert.equal(
    dependencyConnectionRejection({ sourceId: "c", targetId: "a" }, nodes),
    "would-cycle",
  );
  assert.equal(
    dependencyConnectionRejection({ sourceId: "b", targetId: "a" }, nodes),
    "would-cycle",
  );
  // The same pair the other way round is the edge that already exists.
  assert.equal(
    dependencyConnectionRejection({ sourceId: "a", targetId: "b" }, nodes),
    "already-declared",
  );
}

/* An existing cycle elsewhere in the lane must not hang the traversal. */
{
  const nodes = graph(
    node({ id: "a", scheduled_deps: ["b"] }),
    node({ id: "b", scheduled_deps: ["a"] }),
    node({ id: "fresh" }),
  );
  assert.equal(
    dependencyConnectionRejection({ sourceId: "a", targetId: "fresh" }, nodes),
    null,
  );
}

/* An op node may be depended upon — it just cannot hold dependencies. Those are
 * two separate rules and only the second is a rejection. */
{
  const nodes = graph(
    node({ id: "commit-op", kind: "op", state: "done" }),
    node({ id: "dst", state: "virtual" }),
  );
  assert.equal(
    dependencyConnectionRejection(
      { sourceId: "commit-op", targetId: "dst" },
      nodes,
    ),
    null,
  );
}

/* Withdrawing follows the same gate as declaring, plus the edge having to exist
 * in the first place. */
{
  const nodes = graph(
    node({ id: "src" }),
    node({ id: "dst", scheduled_deps: ["src"] }),
    node({ id: "done-dst", state: "done", scheduled_deps: ["src"] }),
    node({
      id: "stale-dst",
      obsolete_reason: "superseded",
      scheduled_deps: ["src"],
    }),
    node({ id: "op-dst", kind: "op", scheduled_deps: ["src"] }),
    node({ id: "unrelated", scheduled_deps: [] }),
  );
  assert.equal(canDisconnectDependency("src", "dst", nodes), true);
  assert.equal(canDisconnectDependency("src", "done-dst", nodes), false);
  assert.equal(canDisconnectDependency("src", "stale-dst", nodes), false);
  assert.equal(canDisconnectDependency("src", "op-dst", nodes), false);
  assert.equal(canDisconnectDependency("src", "unrelated", nodes), false);
  assert.equal(canDisconnectDependency("src", "ghost", nodes), false);
}

/* The written array. Withdrawing removes exactly one id and leaves the rest in
 * order — dependency order reaches the launch prompt, so it is not incidental.
 * Nothing else about the node is in the payload: notably resume_from_node_id,
 * which is an independent relation the template editor happens to clear
 * alongside a disconnect but the runtime must not. */
{
  const target = node({
    id: "dst",
    scheduled_deps: ["a", "b", "c"],
    resume_from_node_id: "b",
  });
  assert.deepEqual(scheduledDepsAfterDisconnect(target, "b"), ["a", "c"]);
  assert.deepEqual(scheduledDepsAfterDisconnect(target, "absent"), [
    "a",
    "b",
    "c",
  ]);
  assert.equal(target.resume_from_node_id, "b");

  assert.deepEqual(scheduledDepsAfterConnect(target, "d"), [
    "a",
    "b",
    "c",
    "d",
  ]);
  assert.deepEqual(scheduledDepsAfterConnect(target, "b"), ["a", "b", "c"]);
  assert.deepEqual(scheduledDepsAfterConnect(node({ id: "empty" }), "a"), ["a"]);
}

/* The id the layout emits for a dependency edge, before collapsed-instance
 * remapping rewrites the endpoints. */
{
  assert.equal(dependencyEdgeId("a", "b"), "dep:a->b");
}

/* Where the wire is released decides what it means. Landing on a node declares
 * a dependency between two nodes that already exist; landing on empty canvas
 * asks for a new downstream node instead. Both are legal outcomes of the same
 * gesture, so `none` is reserved for a release that can write nothing. */
{
  const nodes = graph(
    node({ id: "src", state: "done" }),
    node({ id: "dst", state: "virtual" }),
    node({ id: "ran", state: "done" }),
  );

  assert.deepEqual(resolveWiringDrop("src", { kind: "target", targetId: "dst" }, nodes), {
    kind: "connect",
    sourceId: "src",
    targetId: "dst",
  });
  // Empty canvas: the drop becomes a request for a new dependent node.
  assert.deepEqual(resolveWiringDrop("src", { kind: "canvas" }, nodes), {
    kind: "create",
    sourceId: "src",
  });
  /* Released back on the button it came from. A press the user thought better
   * of must not leave a node behind, so this is not `create`. */
  assert.deepEqual(resolveWiringDrop("src", { kind: "target", targetId: "src" }, nodes), {
    kind: "none",
    reason: "self-reference",
  });
  // An executed node cannot take a new dependency; the rejection is reported.
  assert.deepEqual(resolveWiringDrop("src", { kind: "target", targetId: "ran" }, nodes), {
    kind: "none",
    reason: "target-not-virtual",
  });
  // Already declared, so the drop is a no-op rather than a duplicate write.
  assert.deepEqual(
    resolveWiringDrop(
      "src",
      { kind: "target", targetId: "dep" },
      graph(node({ id: "src" }), node({ id: "dep", scheduled_deps: ["src"] })),
    ),
    { kind: "none", reason: "already-declared" },
  );
  /* A source that left the graph mid-gesture writes nothing — notably it does
   * NOT fall through to `create`, which would make a node depend on an id that
   * no longer exists. */
  assert.deepEqual(resolveWiringDrop("ghost", { kind: "canvas" }, nodes), {
    kind: "none",
    reason: "source-missing",
  });
  /* A null target is no longer allowed to masquerade as empty canvas. UI
   * chrome, sidebars, outside-window releases, and collapsed boxes all cancel. */
  assert.deepEqual(resolveWiringDrop("src", { kind: "blocked" }, nodes), {
    kind: "none",
    reason: "blocked-surface",
  });
}

/* The action stack is centred on the tile's right edge. Nothing competes for
 * that column: every handle is inert, so a button over one costs nothing. */
{
  const offsetOf = (top: string): number => {
    if (top === "50%") return 0;
    const match = /^calc\(50% \+ (-?\d+(?:\.\d+)?)px\)$/.exec(top);
    assert.ok(match, `unexpected stackTop form: ${top}`);
    return Number(match[1]);
  };

  for (let total = 1; total <= 4; total += 1) {
    const offsets = Array.from({ length: total }, (_, index) =>
      offsetOf(stackTop(index, total)),
    );
    // Slots stay ordered and never overlap: 24px tall, 30px apart.
    for (let i = 1; i < offsets.length; i += 1) {
      assert.ok(
        offsets[i] - offsets[i - 1] >= 24,
        `buttons ${i - 1} and ${i} overlap (total=${total})`,
      );
    }
    // Centred: the slots are symmetric about the tile's middle.
    const sum = offsets.reduce((acc, value) => acc + value, 0);
    assert.ok(
      Math.abs(sum) < 0.001,
      `stack is not centred for total=${total}: ${offsets.join(",")}`,
    );
  }

  // A node with no buttons still reports the centre for the remove popover.
  assert.equal(stackTop(-1, 0), "50%");
}

console.log("canvas-wiring: ok");
