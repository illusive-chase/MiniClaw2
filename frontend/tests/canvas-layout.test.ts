import assert from "node:assert/strict";
import { decorateEdges, resolveHoverGroup } from "../src/canvas/edgeVisibility";
import {
  decoratePendingGateLayers,
  PENDING_GATE_NODE_Z_INDEX,
} from "../src/canvas/nodeLayers";
import {
  buildGraph,
  snapPlanspaceChildPosition,
  classifyPlanspaceLaneResizes,
  contextIdentityKey,
  LANE,
  resolveCommitPositionTransfer,
  resolveGitChangesAppearancePosition,
  resizePlanspaceLanes,
  type BuildGraphArgs,
} from "../src/canvas/layout";
import type {
  CommitDescriptor,
  ContextBundle,
  NodeInfo,
} from "../src/types";
import {
  nodeIdsNeedingEventReplay,
  shouldAutoSelectEventNode,
  shouldOpenCreatedPlanspace,
  shouldOpenInteractionNode,
} from "../src/nodeUtil";

const principle = {
  id: "principles.careful",
  slug: "careful",
  title: "Careful",
  description: "Check the work",
  path: "/library/principles/careful",
};
const skill = {
  id: "skills.search",
  slug: "search",
  title: "Search",
  description: "Search files",
  path: "/library/skills/search",
};

function node(id: string, overrides: Partial<NodeInfo> = {}): NodeInfo {
  return {
    id,
    project_id: "project",
    kind: "agent",
    state: "done",
    provider: null,
    prompt: id,
    scheduled_deps: [],
    created_at: 1,
    ...overrides,
  };
}

assert.deepEqual(
  nodeIdsNeedingEventReplay([
    node("virtual", { state: "virtual" }),
    node("queued", { state: "queued" }),
    node("running", { state: "running" }),
    node("waiting", { state: "waiting" }),
    node("review", { state: "awaiting_human_input" }),
    node("done", { state: "done" }),
  ]),
  ["queued", "running", "waiting", "review"],
  "queued nodes must request replay before their buffered node_started event can be delivered",
);

assert.equal(
  shouldAutoSelectEventNode({ kind: "none" }),
  true,
  "runner events may surface a node when the user has no selection",
);
assert.equal(
  shouldAutoSelectEventNode({ kind: "agent" }),
  false,
  "runner events must not replace another selected node",
);
assert.equal(
  shouldAutoSelectEventNode({ kind: "context" }),
  false,
  "runner events must preserve non-agent editor selections too",
);
assert.equal(
  shouldOpenInteractionNode({ kind: "agent", nodeId: "review" }, "review"),
  true,
  "an interaction must reopen controls for the already-selected node",
);
assert.equal(
  shouldOpenInteractionNode({ kind: "agent", nodeId: "draft" }, "review"),
  false,
  "an interaction must not replace a different selected node",
);
assert.equal(
  shouldOpenInteractionNode({ kind: "artifact", nodeId: "review" }, "review"),
  false,
  "an interaction must preserve a non-execution selection",
);
assert.equal(
  shouldOpenCreatedPlanspace(true),
  true,
  "an idle planspace creation should open its seeded node",
);
assert.equal(
  shouldOpenCreatedPlanspace(false),
  false,
  "a background planspace creation must preserve the current selection",
);

function commit(
  sha: string,
  externalCount = 0,
  overrides: Partial<CommitDescriptor> = {},
): CommitDescriptor {
  return {
    sha,
    live: true,
    message: sha,
    external_count_before: externalCount,
    aliases: [],
    ...overrides,
  };
}

function testCommitBranchesUseParentsAndColumns(): void {
  const graph = buildGraph(args({
    gitCommits: [
      commit("base", 0, { column: 0, parent_shas: [] }),
      commit("local", 0, { column: 0, parent_shas: ["base"] }),
      commit("peer-a", 0, {
        column: 1,
        parent_shas: ["base"],
        availability: "peer",
        host_ids: ["host-b"],
      }),
      commit("peer-b", 0, {
        column: 1,
        parent_shas: ["peer-a"],
        availability: "peer",
        host_ids: ["host-b"],
      }),
    ],
    gitHosts: [{
      mid: "host-b",
      label: "workstation",
      head: "peer-b",
      recorded_at: 1,
      dirty: true,
    }],
  }));

  assert.equal(graph.rfEdges.some((edge) => edge.id === "commit-trunk:base:local"), true);
  assert.equal(graph.rfEdges.some((edge) => edge.id === "commit-trunk:base:peer-a"), true);
  assert.equal(graph.rfEdges.some((edge) => edge.id === "commit-trunk:peer-a:peer-b"), true);
  assert.equal(graph.rfEdges.some((edge) => edge.id === "commit-trunk:local:peer-a"), false);
  assert.equal(
    graph.rfNodes.find((item) => item.id === "commit:peer-a")?.position.x,
    LANE.trunkX + LANE.trunkColumnStep,
  );
  assert.equal(
    graph.rfNodes.find((item) => item.id === "commit:peer-a")?.position.y,
    LANE.trunkStartY + LANE.trunkStep,
  );
  assert.equal(graph.rfNodes.some((item) => item.id === "commit-column:1"), true);
  assert.equal(graph.rfNodes.some((item) => item.id === "commit:ghost"), false);
}

function testPeerCommitRowsFollowVisibleParents(): void {
  const graph = buildGraph(args({
    gitCommits: [
      commit("base", 0, { column: 0, parent_shas: [] }),
      commit("local-a", 0, { column: 0, parent_shas: ["base"] }),
      commit("local-b", 0, { column: 0, parent_shas: ["local-a"] }),
      commit("peer", 0, {
        column: 1,
        parent_shas: ["local-b"],
        availability: "peer",
        host_ids: ["host-b"],
      }),
    ],
  }));
  const parent = graph.rfNodes.find((item) => item.id === "commit:local-b");
  const child = graph.rfNodes.find((item) => item.id === "commit:peer");

  assert.equal(parent?.position.y, LANE.trunkStartY + 2 * LANE.trunkStep);
  assert.equal(child?.position.y, LANE.trunkStartY + 3 * LANE.trunkStep);
}

function testCommitFallbackDoesNotCrossColumnsAndHintsWin(): void {
  const hinted = { x: 700, y: 320 };
  const graph = buildGraph(args({
    gitCommits: [
      commit("local", 0, { column: 0 }),
      commit("peer-a", 0, { column: 1, availability: "peer" }),
      commit("peer-b", 0, { column: 1, availability: "peer" }),
    ],
    layoutHints: { "commit:peer-a": hinted },
  }));

  assert.deepEqual(
    graph.rfNodes.find((item) => item.id === "commit:peer-a")?.position,
    hinted,
  );
  assert.equal(graph.rfEdges.some((edge) => edge.id === "commit-trunk:local:peer-a"), false);
  assert.equal(graph.rfEdges.some((edge) => edge.id === "commit-trunk:peer-a:peer-b"), true);
}

function args(overrides: Partial<BuildGraphArgs> = {}): BuildGraphArgs {
  return {
    nodes: [],
    activeNodeIds: [],
    layoutHints: {},
    contextBundlesByNodeId: {},
    knownPlanspaceIds: [],
    activatablePlanspaceIds: [],
    hiddenPlanspaceIds: [],
    activePlanspaceId: null,
    autoPlanspaceIds: [],
    canCreateVirtual: true,
    principles: [principle],
    skills: [skill],
    gitCommits: [],
    gitHead: null,
    gitDirtyCount: 0,
    ...overrides,
  };
}

function bundle(ownerId: string, kind: string, path: string, plugId: string): ContextBundle {
  return {
    bundle_id: `bundle-${ownerId}`,
    created_at: 1,
    node_id: ownerId,
    sources: [{
      scope: "contextspace",
      kind,
      path,
      sha256: "hash",
      chars: 42,
      injection: "system",
      plug_id: plugId,
    }],
  };
}

function contextNodes(graph: ReturnType<typeof buildGraph>) {
  return graph.rfNodes.filter((item) => item.type === "context");
}

function testNoRootOrFabricatedDependencies(): void {
  const graph = buildGraph(args({ nodes: [node("solo")] }));
  assert.equal(graph.rfNodes.some((item) => item.id === "root"), false);
  assert.equal(graph.rfEdges.some((edge) => edge.source === "root"), false);
  assert.equal(graph.rfEdges.some((edge) => edge.type === "dependency"), false);

  const withDep = buildGraph(args({
    nodes: [node("a"), node("b", { scheduled_deps: ["a"] })],
  }));
  assert.equal(withDep.rfEdges.some((edge) => edge.id === "dep:a->b"), true);
}

function testKnownLaneOrderSurvivesNodeCreationOrder(): void {
  const knownPlanspaceIds = ["planspaces.alpha", "planspaces.beta"];
  const empty = buildGraph(args({ knownPlanspaceIds }));
  const populated = buildGraph(args({
    nodes: [
      node("beta-first", { planspace_id: "planspaces.beta", created_at: 1 }),
      node("legacy-second", { planspace_id: "planspaces.legacy", created_at: 2 }),
      node("alpha-last", { planspace_id: "planspaces.alpha", created_at: 3 }),
    ],
    knownPlanspaceIds,
  }));
  const lanes = (graph: ReturnType<typeof buildGraph>) =>
    graph.rfNodes.filter((item) => item.type === "planspaceLane");

  assert.deepEqual(
    lanes(populated).map((item) => item.id),
    [
      "planspace:planspaces.alpha",
      "planspace:planspaces.beta",
      "planspace:planspaces.legacy",
    ],
  );
  assert.deepEqual(
    lanes(populated).slice(0, 2).map((item) => item.position.x),
    lanes(empty).map((item) => item.position.x),
  );
  assert.ok(lanes(populated)[1].position.y > lanes(populated)[0].position.y);
}

function testInactiveAutoLaneIsMarkedForActivation(): void {
  const graph = buildGraph(args({
    knownPlanspaceIds: ["planspaces.auto"],
    activatablePlanspaceIds: ["planspaces.auto"],
    autoPlanspaceIds: ["planspaces.auto"],
    activePlanspaceId: null,
  }));
  const lane = graph.rfNodes.find((item) => item.id === "planspace:planspaces.auto");
  assert.equal(lane?.type, "planspaceLane");
  if (lane?.type !== "planspaceLane") throw new Error("missing planspace lane");
  assert.equal(lane.data.active, false);
  assert.equal(lane.data.auto, true);
  assert.equal(lane.data.canActivate, true);
  assert.deepEqual(lane.style, { pointerEvents: "none" });
}

function testPlanspaceChildPositionUsesLaneRelativeSnapGrid(): void {
  assert.deepEqual(
    snapPlanspaceChildPosition({ x: 146, y: 289 }, { x: 100, y: 200 }),
    { x: 48, y: 88 },
  );
  assert.deepEqual(
    snapPlanspaceChildPosition({ x: 105, y: 211 }, { x: 100, y: 200 }),
    { x: 40, y: 40 },
    "new nodes must stay inside the planspace child extent",
  );
}

function testProjectScopedLaneLabelShowsOnlyDirectionName(): void {
  const graph = buildGraph(args({
    knownPlanspaceIds: ["planspaces.miniclaw2-dev.naming-fix"],
  }));
  const lane = graph.rfNodes.find(
    (item) => item.id === "planspace:planspaces.miniclaw2-dev.naming-fix",
  );

  assert.equal(
    (lane?.data as { label?: string } | undefined)?.label,
    "Naming Fix",
  );
}

function testVerticalCommitTrunkAndStableLaneX(): void {
  const work = node("work", { planspace_id: "planspaces.alpha" });
  const one = buildGraph(args({
    nodes: [work],
    knownPlanspaceIds: ["planspaces.alpha"],
    gitCommits: [commit("a", 3)],
  }));
  const two = buildGraph(args({
    nodes: [work],
    knownPlanspaceIds: ["planspaces.alpha"],
    gitCommits: [commit("a", 3), commit("b", 2)],
    gitDirtyCount: 1,
  }));
  const hubs = two.rfNodes.filter((item) => item.type === "commit");
  assert.deepEqual(hubs.map((item) => item.position.x), [LANE.trunkX, LANE.trunkX, LANE.trunkX]);
  assert.deepEqual(hubs.map((item) => item.position.y), [
    LANE.trunkStartY,
    LANE.trunkStartY + LANE.trunkStep,
    LANE.trunkStartY + LANE.trunkStep * 2,
  ]);
  assert.equal(
    one.rfNodes.find((item) => item.id === "planspace:planspaces.alpha")?.position.x,
    two.rfNodes.find((item) => item.id === "planspace:planspaces.alpha")?.position.x,
  );
  assert.equal((hubs[0].data as { externalCountBefore?: number }).externalCountBefore, 3);
  assert.equal(two.rfEdges.find((edge) => edge.id.includes(":a:b"))?.data?.externalCount, 2);

  const empty = buildGraph(args({ gitDirtyCount: 2 }));
  assert.deepEqual(empty.rfNodes.map((item) => item.id), ["commit:ghost"]);
  assert.equal(empty.rfEdges.length, 0);
}

function testChangesNodePreservesSavedPosition(): void {
  const headPosition = { x: 360, y: 520 };
  const savedChangesPosition = { x: 40, y: 80 };
  const graph = buildGraph(args({
    gitCommits: [commit("base"), commit("head")],
    gitHead: "head",
    gitDirtyCount: 2,
    layoutHints: {
      "commit:head": headPosition,
      "commit:ghost": savedChangesPosition,
    },
  }));

  assert.deepEqual(
    graph.rfNodes.find((item) => item.id === "commit:ghost")?.position,
    savedChangesPosition,
  );
  assert.equal(
    graph.rfEdges.some((edge) => edge.id === "commit-trunk:head:ghost"),
    true,
  );
}

function testChangesNodeAvoidsPostHeadRows(): void {
  const graph = buildGraph(args({
    gitCommits: [commit("base"), commit("head"), commit("stale")],
    gitHead: "head",
    gitDirtyCount: 1,
  }));
  const stalePosition = graph.rfNodes.find(
    (item) => item.id === "commit:stale",
  )?.position;

  assert.ok(stalePosition);
  assert.deepEqual(
    graph.rfNodes.find((item) => item.id === "commit:ghost")?.position,
    { x: LANE.trunkX, y: stalePosition.y + LANE.trunkStep },
  );
}

function testAppearingChangesNodeAvoidsPostHeadRows(): void {
  const current = buildGraph(args({
    gitCommits: [commit("base"), commit("head"), commit("stale")],
    gitHead: "head",
  })).rfNodes;
  const next = buildGraph(args({
    gitCommits: [commit("base"), commit("head"), commit("stale")],
    gitHead: "head",
    gitDirtyCount: 1,
    layoutHints: { "commit:ghost": { x: 40, y: 80 } },
  })).rfNodes;
  const stalePosition = next.find(
    (item) => item.id === "commit:stale",
  )?.position;

  assert.ok(stalePosition);
  assert.deepEqual(resolveGitChangesAppearancePosition(current, next), {
    x: LANE.trunkX,
    y: stalePosition.y + LANE.trunkStep,
  });
  assert.equal(resolveGitChangesAppearancePosition(next, next), null);
}

function testCommitLayoutResolvesShaAliases(): void {
  const aliasedCommit = {
    ...commit("rebased"),
    aliases: ["oldest", "old"],
  };
  const aliasPosition = { x: 360, y: 520 };
  const fallback = buildGraph(args({
    gitCommits: [aliasedCommit],
    layoutHints: { "commit:old": aliasPosition },
  }));

  assert.deepEqual(
    fallback.rfNodes.find((item) => item.id === "commit:rebased")?.position,
    aliasPosition,
  );

  const firstAliasPosition = { x: 240, y: 400 };
  const ordered = buildGraph(args({
    gitCommits: [aliasedCommit],
    layoutHints: {
      "commit:oldest": firstAliasPosition,
      "commit:old": aliasPosition,
    },
  }));
  assert.deepEqual(
    ordered.rfNodes.find((item) => item.id === "commit:rebased")?.position,
    firstAliasPosition,
  );
}

function testCurrentCommitLayoutWinsOverShaAliases(): void {
  const currentPosition = { x: 480, y: 640 };
  const graph = buildGraph(args({
    gitCommits: [{ ...commit("rebased"), aliases: ["old"] }],
    layoutHints: {
      "commit:rebased": currentPosition,
      "commit:old": { x: 360, y: 520 },
    },
  }));

  assert.deepEqual(
    graph.rfNodes.find((item) => item.id === "commit:rebased")?.position,
    currentPosition,
  );
}

function testCommittedGhostTransfersItsPositionToNewHead(): void {
  const before = buildGraph(args({
    gitCommits: [commit("old")],
    gitHead: "old",
    gitDirtyCount: 2,
  })).rfNodes.map((item) =>
    item.id === "commit:ghost"
      ? { ...item, position: { x: 360, y: 520 } }
      : item,
  );
  const after = buildGraph(args({
    gitCommits: [commit("old"), commit("new")],
    gitHead: "new",
  })).rfNodes;

  assert.deepEqual(resolveCommitPositionTransfer(before, after, "new"), {
    fromId: "commit:ghost",
    toId: "commit:new",
    position: { x: 360, y: 520 },
    resetGhostPosition: null,
  });
}

function testRemainingChangesMoveToTheNextCommitSlot(): void {
  const before = buildGraph(args({
    gitCommits: [commit("old")],
    gitHead: "old",
    gitDirtyCount: 2,
  })).rfNodes.map((item) =>
    item.id === "commit:ghost"
      ? { ...item, position: { x: 360, y: 520 } }
      : item,
  );
  const after = buildGraph(args({
    gitCommits: [commit("old"), commit("new")],
    gitHead: "new",
    gitDirtyCount: 1,
    layoutHints: { "commit:ghost": { x: 360, y: 520 } },
  })).rfNodes;

  assert.deepEqual(resolveCommitPositionTransfer(before, after, "new"), {
    fromId: "commit:ghost",
    toId: "commit:new",
    position: { x: 360, y: 520 },
    resetGhostPosition: {
      x: 360,
      y: 520 + LANE.trunkStep,
    },
  });
}

function testAlreadyRenderedCommitUsesRetainedGhostPosition(): void {
  const rendered = buildGraph(args({
    gitCommits: [commit("old"), commit("new")],
    gitHead: "new",
  })).rfNodes;

  assert.deepEqual(
    resolveCommitPositionTransfer(
      rendered,
      rendered,
      "new",
      { x: 360, y: 520 },
    ),
    {
      fromId: "commit:ghost",
      toId: "commit:new",
      position: { x: 360, y: 520 },
      resetGhostPosition: null,
    },
  );
}

function testAlreadyRenderedCommitDoesNotUseRemainingChangesPosition(): void {
  const rendered = buildGraph(args({
    gitCommits: [commit("old"), commit("new")],
    gitHead: "new",
    gitDirtyCount: 1,
  })).rfNodes.map((item) =>
    item.id === "commit:ghost"
      ? { ...item, position: { x: 480, y: 680 } }
      : item,
  );

  assert.deepEqual(
    resolveCommitPositionTransfer(
      rendered,
      rendered,
      "new",
      { x: 360, y: 520 },
    ),
    {
      fromId: "commit:ghost",
      toId: "commit:new",
      position: { x: 360, y: 520 },
      resetGhostPosition: {
        x: 360,
        y: 520 + LANE.trunkStep,
      },
    },
  );
}

function testCleaningWithoutACommitDoesNotMoveHead(): void {
  const before = buildGraph(args({
    gitCommits: [commit("head")],
    gitHead: "head",
    gitDirtyCount: 1,
  })).rfNodes;
  const after = buildGraph(args({
    gitCommits: [commit("head")],
    gitHead: "head",
  })).rfNodes;

  assert.equal(resolveCommitPositionTransfer(before, after, null), null);
}

function testUnrelatedHeadChangeDoesNotTransferGhost(): void {
  const before = buildGraph(args({
    gitCommits: [commit("old")],
    gitHead: "old",
    gitDirtyCount: 2,
  })).rfNodes.map((item) =>
    item.id === "commit:ghost"
      ? { ...item, position: { x: 360, y: 520 } }
      : item,
  );
  const after = buildGraph(args({
    gitCommits: [commit("branch-head")],
    gitHead: "branch-head",
    gitDirtyCount: 2,
  })).rfNodes;

  assert.equal(resolveCommitPositionTransfer(before, after, null), null);
  assert.equal(resolveCommitPositionTransfer(before, after, "different-commit"), null);
  assert.equal(
    (after.find((item) => item.id === "commit:branch-head")?.data as { head?: boolean }).head,
    true,
  );
  assert.ok(after.some((item) => item.id === "commit:ghost"));
}

function testEpochLinksAndHoverGroups(): void {
  const graph = buildGraph(args({
    nodes: [node("worker", { commit_before: "a", commit_after: "b" })],
    gitCommits: [commit("a"), commit("b")],
  }));
  assert.deepEqual(graph.epochMembersByCommitSha, { a: ["worker"] });
  assert.deepEqual(graph.commitHubIdByNodeId, { worker: "commit:a" });
  const source = graph.rfEdges.find((edge) => edge.id.startsWith("commit-source"));
  const sink = graph.rfEdges.find((edge) => edge.id.startsWith("commit-sink"));
  assert.equal(source?.type, "commitLink");
  assert.equal(sink?.data?.dashed, true);
  /* Epoch links enter the agent tile's top and leave its bottom, so they read
   * against the vertical trunk instead of the horizontal dep axis. */
  assert.equal(source?.targetHandle, "epochIn");
  assert.equal(sink?.sourceHandle, "epochOut");
  assert.deepEqual(resolveHoverGroup("commit:a", graph.epochMembersByCommitSha, graph.commitHubIdByNodeId), ["commit:a", "worker"]);
  assert.deepEqual(resolveHoverGroup("worker", graph.epochMembersByCommitSha, graph.commitHubIdByNodeId), ["worker", "commit:a"]);
}

function testBindingDrivenContextTiles(): void {
  const unbound = buildGraph(args({ nodes: [node("run")] }));
  assert.equal(contextNodes(unbound).length, 0);

  const declaredNode = node("draft", {
    state: "virtual",
    pending_extra_principles: [principle.id],
    pending_extra_skills: [{ id: skill.id, suggest: false }],
  });
  const declared = buildGraph(args({ nodes: [declaredNode] }));
  assert.equal(contextNodes(declared).length, 2);
  const declaredLoads = declared.rfEdges.filter((edge) => edge.type === "loads");
  assert.equal(declaredLoads.every((edge) => edge.data?.relation === "declared"), true);
  /* Loads run tile-left → agent-top. */
  assert.equal(
    declaredLoads.every((edge) => edge.sourceHandle === "loads" && edge.targetHandle === "loads"),
    true,
  );
  const hiddenOp = buildGraph(args({
    nodes: [node("shell", { kind: "op", state: "running" })],
    contextBundlesByNodeId: {
      shell: bundle("shell", "principle", `${principle.path}/CONTEXT.md`, principle.id),
    },
  }));
  assert.equal(hiddenOp.rfNodes.some((item) => item.id === "shell"), false);
  assert.equal(contextNodes(hiddenOp).length, 0);
  assert.equal(hiddenOp.rfEdges.some((edge) => edge.target === "shell"), false);

  const principlePath = `${principle.path}/CONTEXT.md`;
  const observed = buildGraph(args({
    nodes: [node("run")],
    contextBundlesByNodeId: {
      run: bundle("run", "principle", principlePath, principle.id),
    },
  }));
  assert.equal(observed.rfEdges.find((edge) => edge.type === "loads")?.data?.relation, "used");
  assert.equal(contextNodes(observed)[0].id, `ctx:${contextIdentityKey("contextspace", "principle", principlePath)}`);

  const availableSkill = buildGraph(args({
    nodes: [node("run", { settings_snapshot: { skill_audit: [{ id: skill.id, used: false }] } })],
  }));
  assert.equal(availableSkill.rfEdges.find((edge) => edge.type === "loads")?.data?.relation, "available");
  const usedSkill = buildGraph(args({
    nodes: [node("run", { settings_snapshot: { skill_audit: [{ id: skill.id, used: true }] } })],
  }));
  assert.equal(usedSkill.rfEdges.find((edge) => edge.type === "loads")?.data?.relation, "used");

  const hidden = buildGraph(args({
    nodes: [node("draft", {
      state: "virtual",
      planspace_id: "planspaces.hidden",
      pending_extra_principles: [principle.id],
    })],
    hiddenPlanspaceIds: ["planspaces.hidden"],
  }));
  assert.equal(contextNodes(hidden).length, 0);
}

function testFloatingContextDoesNotOverlapFirstLane(): void {
  const run = node("run", { planspace_id: "planspaces.alpha" });
  const principlePath = `${principle.path}/CONTEXT.md`;
  const graph = buildGraph(args({
    nodes: [run],
    knownPlanspaceIds: ["planspaces.alpha"],
    contextBundlesByNodeId: {
      run: {
        bundle_id: "bundle-run",
        created_at: 1,
        node_id: "run",
        sources: [
          {
            scope: "contextspace",
            kind: "context",
            path: "/planspaces/alpha/CONTEXT.md",
            sha256: "planspace-hash",
            chars: 42,
            injection: "system",
            plug_id: "planspaces.alpha",
          },
          {
            scope: "contextspace",
            kind: "principle",
            path: principlePath,
            sha256: "principle-hash",
            chars: 42,
            injection: "system",
            plug_id: principle.id,
          },
        ],
      },
    },
  }));
  const lane = graph.rfNodes.find((item) => item.id === "planspace:planspaces.alpha");
  const owned = contextNodes(graph).find((item) => item.parentNode === lane?.id);
  const floating = contextNodes(graph).find((item) => !item.parentNode);
  assert.ok(lane);
  assert.ok(owned);
  assert.ok(floating);
  const ownedLeft = lane.position.x + owned.position.x;
  const ownedRight = ownedLeft + (owned.width ?? 160);
  const floatingLeft = floating.position.x;
  const floatingRight = floatingLeft + (floating.width ?? 160);
  assert.equal(floatingRight <= ownedLeft || floatingLeft >= ownedRight, true);
}

function testPlanspaceChildrenHaveOneSidedExtent(): void {
  const failed = node("failed", {
    planspace_id: "planspaces.alpha",
    state: "error",
    error: "boom",
    artifacts: [{
      name: "report.md",
      bytes: 42,
      mtime: 1,
      sha256: "report-hash",
      status: "published",
    }],
  });
  const graph = buildGraph(args({
    nodes: [
      failed,
      node("running-op", {
        kind: "op",
        state: "running",
        planspace_id: "planspaces.alpha",
      }),
    ],
    knownPlanspaceIds: ["planspaces.alpha"],
    contextBundlesByNodeId: {
      failed: bundle(
        "failed",
        "planspace",
        "/planspaces/alpha/CONTEXT.md",
        "planspaces.alpha",
      ),
    },
  }));
  const children = graph.rfNodes.filter(
    (item) => item.parentNode === "planspace:planspaces.alpha",
  );

  assert.deepEqual(
    new Set(children.map((item) => item.type)),
    new Set(["agent", "errorTerminal", "artifact", "context"]),
  );
  assert.equal(graph.rfNodes.some((item) => item.id === "running-op"), false);
  for (const child of children) {
    assert.notEqual(child.extent, "parent");
    assert.deepEqual(child.extent?.[0], [
      LANE.planspaceLanePaddingX,
      LANE.planspaceLanePaddingY,
    ]);
    assert.equal(child.extent?.[1][0], Number.POSITIVE_INFINITY);
    assert.equal(child.extent?.[1][1], Number.POSITIVE_INFINITY);
  }
}

function testNewLaneNodeFollowsActualLayout(): void {
  const planspaceId = "planspaces.alpha";
  const graph = buildGraph(args({
    nodes: [
      node("old-1", { planspace_id: planspaceId, created_at: 1 }),
      node("old-2", { planspace_id: planspaceId, created_at: 2 }),
      node("old-3", { planspace_id: planspaceId, created_at: 3 }),
      node("old-4", { planspace_id: planspaceId, created_at: 4 }),
      node("new", { planspace_id: planspaceId, created_at: 5 }),
    ],
    knownPlanspaceIds: [planspaceId],
    layoutHints: {
      "old-1": { x: 40, y: LANE.planspaceLaneAgentRowY },
      "old-2": { x: 320, y: LANE.planspaceLaneAgentRowY },
      "old-3": { x: 40, y: LANE.planspaceLaneAgentRowY + LANE.siblingYStep },
      "old-4": { x: 320, y: LANE.planspaceLaneAgentRowY + LANE.siblingYStep },
    },
  }));

  assert.deepEqual(
    graph.rfNodes.find((item) => item.id === "new")?.position,
    { x: 600, y: LANE.planspaceLaneAgentRowY },
  );
}

function testRerunNodeCascadesNearOriginal(): void {
  const planspaceId = "planspaces.alpha";
  const originalPosition = { x: 320, y: 480 };
  const graph = buildGraph(args({
    nodes: [
      node("dependency", { planspace_id: planspaceId, created_at: 1 }),
      node("failed", {
        planspace_id: planspaceId,
        state: "error",
        scheduled_deps: ["dependency"],
        created_at: 2,
      }),
      node("rerun", {
        planspace_id: planspaceId,
        state: "queued",
        scheduled_deps: ["dependency"],
        proposed_by: "rerun:failed",
        created_at: 3,
      }),
    ],
    knownPlanspaceIds: [planspaceId],
    layoutHints: { failed: originalPosition },
  }));

  assert.deepEqual(
    graph.rfNodes.find((item) => item.id === "rerun")?.position,
    { x: originalPosition.x + 24, y: originalPosition.y + 24 },
  );

  const draggedPosition = { x: 960, y: 240 };
  const dragged = buildGraph(args({
    nodes: graph.rfNodes
      .filter((item) => item.type === "agent")
      .map((item) => (item.data as { node: NodeInfo }).node),
    knownPlanspaceIds: [planspaceId],
    layoutHints: { failed: originalPosition, rerun: draggedPosition },
  }));
  assert.deepEqual(
    dragged.rfNodes.find((item) => item.id === "rerun")?.position,
    draggedPosition,
  );
}

function testPlanspaceLaneMinimumDoesNotExceedAgentHeight(): void {
  const laneId = "planspace:planspaces.alpha";
  const graph = buildGraph(args({
    nodes: [node("work", { planspace_id: "planspaces.alpha" })],
    knownPlanspaceIds: ["planspaces.alpha"],
  }));
  const lane = graph.rfNodes.find((item) => item.id === laneId);
  const work = graph.rfNodes.find((item) => item.id === "work");

  assert.equal(work?.position.y, LANE.planspaceLaneAgentRowY);
  assert.equal(LANE.planspaceLaneMinHeight, work?.height);

  const measuredHeight = 100;
  const compact = resizePlanspaceLanes(
    graph.rfNodes.map((item) =>
      item.id === "work"
        ? {
            ...item,
            position: { ...item.position, y: LANE.planspaceLanePaddingY },
            height: measuredHeight,
          }
        : item,
    ),
    new Set([laneId]),
    true,
  );
  const compactLane = compact.find((item) => item.id === laneId);
  assert.equal(
    compactLane?.height,
    LANE.planspaceLanePaddingY + measuredHeight + LANE.planspaceLanePaddingY,
  );
  assert.ok((compactLane?.height ?? 0) < (lane?.height ?? 0));
}

function testPlanspaceLaneBuildAndDropShareBottomFit(): void {
  const laneId = "planspace:planspaces.alpha";
  const workY = 480;
  const graph = buildGraph(args({
    nodes: [node("work", { planspace_id: "planspaces.alpha" })],
    knownPlanspaceIds: ["planspaces.alpha"],
    layoutHints: { work: { x: 720, y: workY } },
  }));
  const builtLane = graph.rfNodes.find((item) => item.id === laneId);
  const work = graph.rfNodes.find((item) => item.id === "work");
  assert.ok(builtLane);
  assert.ok(work);
  assert.equal(
    builtLane.height,
    workY + (work.height ?? 0) + LANE.planspaceLanePaddingY,
  );

  const oversized = graph.rfNodes.map((item) =>
    item.id === laneId
      ? {
          ...item,
          height: 1200,
          data: { ...item.data, height: 1200 },
        }
      : item,
  );
  const dropped = resizePlanspaceLanes(oversized, new Set([laneId]), true);
  assert.equal(
    dropped.find((item) => item.id === laneId)?.height,
    builtLane.height,
  );
}

function testPlanspaceLaneLiveGrowthAndDropFit(): void {
  const laneId = "planspace:planspaces.alpha";
  const graph = buildGraph(args({
    nodes: [node("work", { planspace_id: "planspaces.alpha" })],
    knownPlanspaceIds: ["planspaces.alpha"],
  }));
  const movingTargets = classifyPlanspaceLaneResizes(graph.rfNodes, [{
    id: "work",
    type: "position",
    position: { x: 720, y: 480 },
    dragging: true,
  }]);
  assert.deepEqual([...movingTargets.growLaneIds], [laneId]);
  assert.equal(movingTargets.fitLaneIds.size, 0);

  const stoppedTargets = classifyPlanspaceLaneResizes(graph.rfNodes, [{
    id: "work",
    type: "position",
    dragging: false,
  }]);
  assert.equal(stoppedTargets.growLaneIds.size, 0);
  assert.deepEqual([...stoppedTargets.fitLaneIds], [laneId]);

  const measuredTargets = classifyPlanspaceLaneResizes(graph.rfNodes, [{
    id: "work",
    type: "dimensions",
    dimensions: { width: 224, height: 110 },
  }]);
  assert.equal(measuredTargets.growLaneIds.size, 0);
  assert.deepEqual([...measuredTargets.fitLaneIds], [laneId]);

  const moved = graph.rfNodes.map((item) =>
    item.id === "work"
      ? { ...item, position: { x: 720, y: 480 }, width: 224, height: 100 }
      : item,
  );
  const grown = resizePlanspaceLanes(moved, new Set([laneId]), false);
  const grownLane = grown.find((item) => item.id === laneId);
  assert.equal(grownLane?.width, 720 + 224 + LANE.planspaceLanePaddingX);
  assert.equal(grownLane?.height, 480 + 100 + LANE.planspaceLanePaddingY);
  assert.equal((grownLane?.data as { width?: number }).width, grownLane?.width);
  assert.equal((grownLane?.data as { height?: number }).height, grownLane?.height);

  const oversized = grown.map((item) =>
    item.id === laneId
      ? {
          ...item,
          width: 1600,
          height: 1200,
          data: { ...item.data, width: 1600, height: 1200 },
        }
      : item,
  );
  const growthOnly = resizePlanspaceLanes(oversized, new Set([laneId]), false);
  assert.equal(growthOnly.find((item) => item.id === laneId)?.width, 1600);
  assert.equal(growthOnly.find((item) => item.id === laneId)?.height, 1200);

  const fitted = resizePlanspaceLanes(oversized, new Set([laneId]), true);
  assert.equal(fitted.find((item) => item.id === laneId)?.width, grownLane?.width);
  assert.equal(fitted.find((item) => item.id === laneId)?.height, grownLane?.height);
}

function testPlanspaceLaneResizeReflowsLaterAutomaticLanes(): void {
  const firstLaneId = "planspace:planspaces.alpha";
  const secondLaneId = "planspace:planspaces.beta";
  const thirdLaneId = "planspace:planspaces.gamma";
  const layoutHints = { [secondLaneId]: { x: 40, y: 900 } };
  const graph = buildGraph(args({
    nodes: [node("work", { planspace_id: "planspaces.alpha" })],
    knownPlanspaceIds: [
      "planspaces.alpha",
      "planspaces.beta",
      "planspaces.gamma",
    ],
    layoutHints,
  }));
  const originalFirst = graph.rfNodes.find((item) => item.id === firstLaneId);
  const originalSecond = graph.rfNodes.find((item) => item.id === secondLaneId);
  const originalThird = graph.rfNodes.find((item) => item.id === thirdLaneId);
  const originalWork = graph.rfNodes.find((item) => item.id === "work");
  assert.ok(originalFirst);
  assert.ok(originalSecond);
  assert.ok(originalThird);
  assert.ok(originalWork);

  const moved = graph.rfNodes.map((item) =>
    item.id === "work"
      ? { ...item, position: { ...item.position, y: 1_200 }, height: 100 }
      : item,
  );
  const grown = resizePlanspaceLanes(
    moved,
    new Set([firstLaneId]),
    false,
    layoutHints,
  );
  const grownFirst = grown.find((item) => item.id === firstLaneId);
  const grownSecond = grown.find((item) => item.id === secondLaneId);
  const grownThird = grown.find((item) => item.id === thirdLaneId);
  assert.ok(grownFirst);
  assert.ok(grownSecond);
  assert.ok(grownThird);
  assert.equal(grownSecond.position.y, originalSecond.position.y);
  assert.equal(
    grownThird.position.y,
    grownFirst.position.y + (grownFirst.height ?? 0) + LANE.planspaceLaneGap,
  );

  const restoredChild = grown.map((item) =>
    item.id === "work"
      ? {
          ...item,
          position: { ...item.position, y: LANE.planspaceLaneAgentRowY },
          height: originalWork.height,
        }
      : item,
  );
  const fitted = resizePlanspaceLanes(
    restoredChild,
    new Set([firstLaneId]),
    true,
    layoutHints,
  );
  assert.equal(
    fitted.find((item) => item.id === firstLaneId)?.height,
    originalFirst.height,
  );
  assert.equal(
    fitted.find((item) => item.id === secondLaneId)?.position.y,
    originalSecond.position.y,
  );
  assert.equal(
    fitted.find((item) => item.id === thirdLaneId)?.position.y,
    originalThird.position.y,
  );
}

function testPlanspaceLaneReflowsStaleAutomaticPositionWithoutResize(): void {
  const firstLaneId = "planspace:planspaces.alpha";
  const secondLaneId = "planspace:planspaces.beta";
  const layoutHints = { [firstLaneId]: { x: 360, y: -128 } };
  const graph = buildGraph(args({
    nodes: [node("work", { planspace_id: "planspaces.alpha" })],
    knownPlanspaceIds: ["planspaces.alpha", "planspaces.beta"],
    layoutHints,
  }));
  const firstLane = graph.rfNodes.find((item) => item.id === firstLaneId);
  const secondLane = graph.rfNodes.find((item) => item.id === secondLaneId);
  assert.ok(firstLane);
  assert.ok(secondLane);

  const stale = graph.rfNodes.map((item) =>
    item.id === secondLaneId
      ? { ...item, position: { ...item.position, y: 64 } }
      : item,
  );
  const normalized = resizePlanspaceLanes(
    stale,
    new Set([firstLaneId, secondLaneId]),
    true,
    layoutHints,
  );
  const normalizedFirst = normalized.find((item) => item.id === firstLaneId);
  const normalizedSecond = normalized.find((item) => item.id === secondLaneId);

  assert.equal(normalizedFirst, firstLane);
  assert.deepEqual(normalizedFirst?.position, layoutHints[firstLaneId]);
  assert.equal(
    normalizedSecond?.position.y,
    firstLane.position.y + (firstLane.height ?? 0) + LANE.planspaceLaneGap,
  );
  assert.equal(
    resizePlanspaceLanes(
      normalized,
      new Set([firstLaneId, secondLaneId]),
      true,
      layoutHints,
    ),
    normalized,
  );
}

function testObservedSkillMetadataEnrichment(): void {
  const skillPath = `${skill.path}/SKILL.md`;
  const graph = buildGraph(args({
    nodes: [node("run", {
      settings_snapshot: { skill_audit: [{ id: skill.id, used: true }] },
    })],
    contextBundlesByNodeId: {
      run: {
        bundle_id: "bundle-run",
        created_at: 1,
        node_id: "run",
        sources: [{
          scope: "contextspace",
          kind: "skill",
          path: skillPath,
          sha256: "skill-hash",
          chars: 42,
          injection: "system",
        }],
      },
    },
  }));
  const observedSkill = contextNodes(graph).find(
    (item) => item.id === `ctx:${contextIdentityKey("contextspace", "skill", skillPath)}`,
  );
  assert.ok(observedSkill);
  assert.equal((observedSkill.data as { title?: string }).title, skill.title);
  assert.equal((observedSkill.data as { plugId?: string }).plugId, skill.id);
}

function testAutoAttachedSkillsFoldIntoTheirRootTile(): void {
  const workflow = {
    id: "skills.workflow",
    slug: "workflow",
    title: "Workflow",
    description: "Composite workflow",
    path: "/library/skills/workflow",
  };
  const helper = {
    id: "skills.helper",
    slug: "helper",
    title: "Helper",
    description: "Sibling dependency",
    path: "/library/skills/helper",
  };
  const nested = {
    id: "skills.nested",
    slug: "nested",
    title: "Nested",
    description: "Transitive dependency",
    path: "/library/skills/nested",
  };
  const member = {
    id: "skills.member",
    slug: "member",
    title: "Member",
    description: "Skill-pack member",
    path: "/library/skills/member",
  };
  const librarySkills = [skill, workflow, helper, nested, member];
  const audit = [
    { id: workflow.id, used: false },
    {
      id: helper.id,
      used: true,
      auto_attached: true,
      required_by: workflow.id,
      attachment_reason: "dependency",
    },
    {
      id: nested.id,
      used: false,
      auto_attached: true,
      required_by: helper.id,
      attachment_reason: "dependency",
    },
    {
      id: member.id,
      used: false,
      auto_attached: true,
      required_by: workflow.id,
      attachment_reason: "package",
    },
  ];

  /* Dependencies and pack members fold into the explicit root's single tile,
   * including transitive chains (nested ← helper ← workflow). */
  const folded = buildGraph(args({
    nodes: [node("run", { settings_snapshot: { skill_audit: audit } })],
    skills: librarySkills,
  }));
  const tiles = contextNodes(folded);
  assert.equal(tiles.length, 1);
  const tileData = tiles[0].data as {
    plugId?: string;
    attachedSkills?: Array<{ id: string; reason: string; usedByNodeIds: string[] }>;
  };
  assert.equal(tileData.plugId, workflow.id);
  assert.deepEqual(
    (tileData.attachedSkills ?? []).map((item) => item.id).sort(),
    [helper.id, member.id, nested.id].sort(),
  );
  assert.equal(
    tileData.attachedSkills?.find((item) => item.id === member.id)?.reason,
    "package",
  );
  /* A folded dependency being invoked marks the root tile's edge as used. */
  assert.equal(
    folded.rfEdges.find((edge) => edge.type === "loads")?.data?.relation,
    "used",
  );

  /* A skill explicitly loaded elsewhere keeps its own tile, and its direct
   * loads exclude runs where it only rode along as a dependency. */
  const shared = buildGraph(args({
    nodes: [
      node("first", { settings_snapshot: { skill_audit: audit } }),
      node("second", {
        settings_snapshot: { skill_audit: [{ id: helper.id, used: false }] },
      }),
    ],
    skills: librarySkills,
  }));
  const sharedTiles = contextNodes(shared);
  assert.equal(sharedTiles.length, 2);
  const helperTile = sharedTiles.find(
    (item) => (item.data as { plugId?: string }).plugId === helper.id,
  );
  assert.deepEqual(
    (helperTile?.data as { loadedByNodeIds: string[] }).loadedByNodeIds,
    ["second"],
  );

  /* An unresolvable required_by chain fails open onto its own tile. */
  const orphan = buildGraph(args({
    nodes: [node("run", {
      settings_snapshot: {
        skill_audit: [{
          id: helper.id,
          used: false,
          auto_attached: true,
          required_by: "skills.gone",
          attachment_reason: "dependency",
        }],
      },
    })],
    skills: librarySkills,
  }));
  const orphanTiles = contextNodes(orphan);
  assert.equal(orphanTiles.length, 1);
  assert.equal((orphanTiles[0].data as { plugId?: string }).plugId, helper.id);

  /* Declared virtual selections fold identically. */
  const declared = buildGraph(args({
    nodes: [node("draft", {
      state: "virtual",
      pending_extra_skills: [
        { id: workflow.id, suggest: false },
        {
          id: helper.id,
          suggest: false,
          auto_attached: true,
          required_by: workflow.id,
          attachment_reason: "dependency",
        },
      ],
    })],
    skills: librarySkills,
  }));
  const declaredTiles = contextNodes(declared);
  assert.equal(declaredTiles.length, 1);
  assert.deepEqual(
    (declaredTiles[0].data as {
      attachedSkills?: Array<{ id: string }>;
    }).attachedSkills?.map((item) => item.id),
    [helper.id],
  );
  assert.equal(
    declared.rfEdges.find((edge) => edge.type === "loads")?.data?.relation,
    "declared",
  );
}

function testEdgeWeights(): void {
  const graph = buildGraph(args({ nodes: [node("stable")] }));
  const originalNodes = graph.rfNodes;
  const decorated = decorateEdges([
    { id: "dep", source: "a", target: "b", type: "dependency" },
    { id: "trunk", source: "a", target: "b", type: "commitTrunk" },
    { id: "resume", source: "a", target: "b", type: "resume" },
    { id: "error", source: "a", target: "b", type: "timeline" },
    { id: "loads", source: "ctx", target: "a", type: "loads" },
    { id: "produces", source: "a", target: "artifact", type: "produces" },
    { id: "epoch", source: "commit:a", target: "a", type: "commitLink" },
    { id: "other-load", source: "ctx2", target: "z", type: "loads" },
  ], null, []);
  for (const id of ["dep", "trunk", "resume", "error"]) {
    assert.equal(decorated.find((edge) => edge.id === id)?.style?.opacity, undefined);
  }
  for (const id of ["loads", "produces", "epoch", "other-load"]) {
    assert.equal(decorated.find((edge) => edge.id === id)?.style?.opacity, 0);
  }
  const hovered = decorateEdges(decorated, null, ["a", "commit:a"]);
  assert.equal(hovered.find((edge) => edge.id === "loads")?.style?.opacity, 0.75);
  assert.equal(hovered.find((edge) => edge.id === "produces")?.style?.opacity, 0.75);
  assert.equal(hovered.find((edge) => edge.id === "epoch")?.style?.opacity, 0.75);
  assert.equal(hovered.find((edge) => edge.id === "other-load")?.style?.opacity, 0);
  assert.equal(graph.rfNodes, originalNodes);
}

function testPendingGateNodeLayer(): void {
  const graph = buildGraph(args({
    nodes: [
      node("waiting", { state: "waiting", created_at: 1 }),
      node("later-virtual", { state: "virtual", created_at: 2 }),
    ],
  }));
  const layered = decoratePendingGateLayers(graph.rfNodes, ["waiting"]);
  const waiting = layered.find((item) => item.id === "waiting");
  const laterVirtual = layered.find((item) => item.id === "later-virtual");

  assert.equal(waiting?.zIndex, PENDING_GATE_NODE_Z_INDEX);
  assert.equal(laterVirtual?.zIndex, undefined);
  assert.equal(graph.rfNodes.find((item) => item.id === "waiting")?.zIndex, undefined);
  assert.equal(decoratePendingGateLayers(graph.rfNodes, []), graph.rfNodes);
}

testNoRootOrFabricatedDependencies();
testKnownLaneOrderSurvivesNodeCreationOrder();
testInactiveAutoLaneIsMarkedForActivation();
testPlanspaceChildPositionUsesLaneRelativeSnapGrid();
testProjectScopedLaneLabelShowsOnlyDirectionName();
testVerticalCommitTrunkAndStableLaneX();
testChangesNodePreservesSavedPosition();
testChangesNodeAvoidsPostHeadRows();
testAppearingChangesNodeAvoidsPostHeadRows();
testCommitBranchesUseParentsAndColumns();
testPeerCommitRowsFollowVisibleParents();
testCommitFallbackDoesNotCrossColumnsAndHintsWin();
testCommitLayoutResolvesShaAliases();
testCurrentCommitLayoutWinsOverShaAliases();
testCommittedGhostTransfersItsPositionToNewHead();
testRemainingChangesMoveToTheNextCommitSlot();
testAlreadyRenderedCommitUsesRetainedGhostPosition();
testAlreadyRenderedCommitDoesNotUseRemainingChangesPosition();
testCleaningWithoutACommitDoesNotMoveHead();
testUnrelatedHeadChangeDoesNotTransferGhost();
testEpochLinksAndHoverGroups();
testBindingDrivenContextTiles();
testFloatingContextDoesNotOverlapFirstLane();
testPlanspaceChildrenHaveOneSidedExtent();
testNewLaneNodeFollowsActualLayout();
testRerunNodeCascadesNearOriginal();
testPlanspaceLaneMinimumDoesNotExceedAgentHeight();
testPlanspaceLaneBuildAndDropShareBottomFit();
testPlanspaceLaneLiveGrowthAndDropFit();
testPlanspaceLaneResizeReflowsLaterAutomaticLanes();
testPlanspaceLaneReflowsStaleAutomaticPositionWithoutResize();
testObservedSkillMetadataEnrichment();
testAutoAttachedSkillsFoldIntoTheirRootTile();
testEdgeWeights();
testPendingGateNodeLayer();
console.log("canvas layout tests passed");
