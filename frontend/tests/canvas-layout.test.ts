import assert from "node:assert/strict";
import { decorateEdges, resolveHoverGroup } from "../src/canvas/edgeVisibility";
import {
  decoratePendingGateLayers,
  PENDING_GATE_NODE_Z_INDEX,
} from "../src/canvas/nodeLayers";
import {
  buildGraph,
  clusterTemplateInstances,
  snapPlanspaceChildPosition,
  classifyPlanspaceLaneResizes,
  contextIdentityKey,
  LANE,
  resolveCommitPositionTransfer,
  resolveGitChangesAppearancePosition,
  resizePlanspaceLanes,
  summarizeInstanceArguments,
  summarizeInstanceProgress,
  templateInstanceBoxNodeId,
  TEMPLATE_GROUP_NODE_Z_INDEX,
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

/* ───────── template instance groups ───────── */

const TEMPLATE_LANE = "planspaces.alpha";

/** Three stamped members: two roots and one that depends on both. */
function instanceNodes(
  instanceId = "inst-1",
  overrides: Partial<NodeInfo> = {},
): NodeInfo[] {
  return [
    node("tpl-a", {
      planspace_id: TEMPLATE_LANE,
      template_instance_id: instanceId,
      state: "virtual",
      created_at: 1,
      ...overrides,
    }),
    node("tpl-b", {
      planspace_id: TEMPLATE_LANE,
      template_instance_id: instanceId,
      state: "virtual",
      created_at: 2,
      ...overrides,
    }),
    node("tpl-sink", {
      planspace_id: TEMPLATE_LANE,
      template_instance_id: instanceId,
      state: "virtual",
      scheduled_deps: ["tpl-a", "tpl-b"],
      created_at: 3,
      ...overrides,
    }),
  ];
}

function instanceRecord(instanceId = "inst-1") {
  return {
    instance_id: instanceId,
    template_slug: "payment-refactor",
    template_name: "Payment Refactor",
    arguments: { topic: "支付重构" },
    input_bindings: {},
    created_at: 10,
    parent_instance_id: null,
  };
}

function templateArgs(overrides: Partial<BuildGraphArgs> = {}): BuildGraphArgs {
  return args({
    nodes: instanceNodes(),
    knownPlanspaceIds: [TEMPLATE_LANE],
    templateInstances: [instanceRecord()],
    ...overrides,
  });
}

function testSameInstanceNodesClusterTogether(): void {
  const graph = buildGraph(templateArgs());
  const positions = ["tpl-a", "tpl-b", "tpl-sink"].map((id) => {
    const found = graph.rfNodes.find((item) => item.id === id);
    assert.ok(found, `${id} must be placed`);
    return found.position;
  });

  /* Members share the agent row and step by exactly one agent slot: without
   * clustering, `tpl-sink` would be placed relative to its dependency by
   * placeAnchoredVirtualInLane and drop a row instead. */
  for (const position of positions) {
    assert.equal(position.y, LANE.planspaceLaneAgentRowY);
  }
  assert.equal(positions[1].x - positions[0].x, LANE.agentSpacing);
  assert.equal(positions[2].x - positions[1].x, LANE.agentSpacing);

  const frame = graph.rfNodes.find((item) => item.type === "templateGroup");
  assert.ok(frame, "an expanded instance must render a group frame");
  assert.equal(frame.data.instanceId, "inst-1");
  assert.equal(frame.parentNode, `planspace:${TEMPLATE_LANE}`);
  assert.equal(frame.draggable, false);
  assert.deepEqual(frame.style, { pointerEvents: "none" });
  assert.equal(frame.zIndex, TEMPLATE_GROUP_NODE_Z_INDEX);
  /* Behind its members but in front of the lane background. */
  assert.ok(frame.zIndex! > -20 && frame.zIndex! < 0);

  /* The frame must enclose every member on all four sides. */
  const frameRight = frame.position.x + (frame.width ?? 0);
  const frameBottom = frame.position.y + (frame.height ?? 0);
  for (const position of positions) {
    assert.ok(frame.position.x <= position.x, "frame starts left of its members");
    assert.ok(frame.position.y <= position.y, "frame starts above its members");
    assert.ok(frameRight >= position.x + LANE.agentWidth);
    assert.ok(frameBottom >= position.y + 86);
  }

  /* Header text comes from the instance record, not the node payloads. */
  assert.equal(frame.data.label, "Payment Refactor");
  assert.deepEqual(frame.data.argumentSummary, [
    { name: "topic", value: "支付重构" },
  ]);
}

function testInstanceGroupDoesNotConsumeExtraLaneSlots(): void {
  /* A node created after the instance must sit clear of the whole block, not
   * inside it — the reserved block is what keeps the lane cursor monotonic. */
  const graph = buildGraph(templateArgs({
    nodes: [
      ...instanceNodes(),
      node("after", { planspace_id: TEMPLATE_LANE, created_at: 4 }),
    ],
  }));
  const frame = graph.rfNodes.find((item) => item.type === "templateGroup");
  const after = graph.rfNodes.find((item) => item.id === "after");
  assert.ok(frame);
  assert.ok(after);
  assert.ok(
    after.position.x >= frame.position.x + (frame.width ?? 0),
    "a later node must not be placed inside the instance frame",
  );
}

function testLayoutHintsOverrideInstanceClustering(): void {
  const dragged = { x: 900, y: 420 };
  const graph = buildGraph(templateArgs({ layoutHints: { "tpl-b": dragged } }));

  assert.deepEqual(
    graph.rfNodes.find((item) => item.id === "tpl-b")?.position,
    dragged,
    "a manual drag must beat cluster placement",
  );
  /* The frame follows the dragged member rather than clipping it. */
  const frame = graph.rfNodes.find((item) => item.type === "templateGroup");
  assert.ok(frame);
  assert.ok(frame.position.x + (frame.width ?? 0) >= dragged.x + LANE.agentWidth);
  assert.ok(frame.position.y + (frame.height ?? 0) >= dragged.y + 86);
}

function testInstanceClusteringSurvivesReversedNodeOrder(): void {
  /* The placement pass is single-pass and order-dependent, so a member that
   * appears before the sibling it depends on used to fall back to cursor
   * placement. Clustering is resolved in a pre-pass, so reversing the input
   * must produce the same relative layout instead of degrading. */
  const forward = buildGraph(templateArgs());
  const reversed = buildGraph(templateArgs({
    nodes: [...instanceNodes()].reverse(),
  }));

  const frameOf = (graph: ReturnType<typeof buildGraph>) =>
    graph.rfNodes.find((item) => item.type === "templateGroup");
  assert.ok(frameOf(reversed), "reversed input must still render one frame");
  assert.equal(
    reversed.rfNodes.filter((item) => item.type === "templateGroup").length,
    1,
  );
  for (const id of ["tpl-a", "tpl-b", "tpl-sink"]) {
    assert.equal(
      reversed.rfNodes.find((item) => item.id === id)?.position.y,
      LANE.planspaceLaneAgentRowY,
      `${id} must stay on the agent row regardless of input order`,
    );
  }
  /* Every member still lands inside the frame. */
  const frame = frameOf(reversed)!;
  for (const id of ["tpl-a", "tpl-b", "tpl-sink"]) {
    const member = reversed.rfNodes.find((item) => item.id === id)!;
    assert.ok(member.position.x >= frame.position.x);
    assert.ok(
      member.position.x + LANE.agentWidth <= frame.position.x + (frame.width ?? 0),
    );
  }
  assert.deepEqual(
    Object.keys(reversed.templateInstances),
    Object.keys(forward.templateInstances),
  );
}

function testInstanceSpanningTwoLanesDegradesGracefully(): void {
  /* A frame cannot span two lanes. Falling back to ordinary placement is the
   * documented degradation — the nodes must still render. */
  const graph = buildGraph(args({
    nodes: [
      node("split-a", {
        planspace_id: TEMPLATE_LANE,
        template_instance_id: "inst-split",
      }),
      node("split-b", {
        planspace_id: "planspaces.beta",
        template_instance_id: "inst-split",
      }),
    ],
    knownPlanspaceIds: [TEMPLATE_LANE, "planspaces.beta"],
  }));

  assert.equal(graph.rfNodes.some((item) => item.type === "templateGroup"), false);
  assert.deepEqual(graph.templateInstances, {});
  for (const id of ["split-a", "split-b"]) {
    assert.ok(graph.rfNodes.find((item) => item.id === id), `${id} must still render`);
  }
}

function testInstanceGeometryFlowsIntoLaneSizing(): void {
  const laneId = `planspace:${TEMPLATE_LANE}`;
  const withInstance = buildGraph(templateArgs());
  const single = buildGraph(args({
    nodes: [node("solo", { planspace_id: TEMPLATE_LANE })],
    knownPlanspaceIds: [TEMPLATE_LANE],
  }));

  const grouped = withInstance.rfNodes.find((item) => item.id === laneId);
  const lone = single.rfNodes.find((item) => item.id === laneId);
  assert.ok(grouped);
  assert.ok(lone);
  assert.ok(
    (grouped.width ?? 0) > (lone.width ?? 0),
    "a lane holding an instance must be wider than one holding a single node",
  );

  /* The lane must contain the frame, not clip it — the frame's geometry has to
   * reach recordChildExtent and the shared resize path. */
  const frame = withInstance.rfNodes.find((item) => item.type === "templateGroup");
  assert.ok(frame);
  assert.ok(
    frame.position.x + (frame.width ?? 0) + LANE.planspaceLanePaddingX <=
      (grouped.width ?? 0),
    "lane width must cover the frame plus padding",
  );
  assert.ok(
    frame.position.y + (frame.height ?? 0) + LANE.planspaceLanePaddingY <=
      (grouped.height ?? 0),
    "lane height must cover the frame plus padding",
  );
  assert.equal((grouped.data as { width?: number }).width, grouped.width);
}

function testSinkDetectionIgnoresExternalDownstream(): void {
  /* A sink has no downstream INSIDE the instance. An outside consumer is
   * exactly what makes it an output, so it must not disqualify it. */
  const graph = buildGraph(templateArgs({
    nodes: [
      ...instanceNodes(),
      node("outside", {
        planspace_id: TEMPLATE_LANE,
        scheduled_deps: ["tpl-sink"],
        created_at: 4,
      }),
    ],
  }));
  const cluster = graph.templateInstances["inst-1"];
  assert.ok(cluster);
  assert.deepEqual(cluster.sinkNodeIds, ["tpl-sink"]);
  assert.deepEqual(cluster.memberNodeIds, ["tpl-a", "tpl-b", "tpl-sink"]);
  assert.equal(cluster.planspaceId, TEMPLATE_LANE);
  assert.equal(cluster.collapsed, false);

  /* Resume edges count as internal downstream too. */
  const resumed = clusterTemplateInstances(
    [
      node("r-a", { planspace_id: TEMPLATE_LANE, template_instance_id: "i" }),
      node("r-b", {
        planspace_id: TEMPLATE_LANE,
        template_instance_id: "i",
        resume_from_node_id: "r-a",
      }),
    ],
    new Map([
      ["r-a", node("r-a", { planspace_id: TEMPLATE_LANE })],
      ["r-b", node("r-b", { planspace_id: TEMPLATE_LANE })],
    ]),
  );
  assert.deepEqual(resumed.get("i")?.sinkNodeIds, ["r-b"]);

  /* Every member is a sink when nothing depends on anything. */
  const parallel = buildGraph(templateArgs({
    nodes: instanceNodes().map((member) =>
      member.id === "tpl-sink" ? { ...member, scheduled_deps: [] } : member,
    ),
  }));
  assert.deepEqual(
    parallel.templateInstances["inst-1"].sinkNodeIds,
    ["tpl-a", "tpl-b", "tpl-sink"],
  );
}

function testCollapsedInstanceRendersOneBoxAndRedirectsEdges(): void {
  const graph = buildGraph(templateArgs({
    nodes: [
      node("upstream", { planspace_id: TEMPLATE_LANE, created_at: 0 }),
      ...instanceNodes().map((member) =>
        member.id === "tpl-a"
          ? { ...member, scheduled_deps: ["upstream"] }
          : member,
      ),
      node("downstream", {
        planspace_id: TEMPLATE_LANE,
        scheduled_deps: ["tpl-sink"],
        created_at: 4,
      }),
    ],
    collapsedTemplateInstanceIds: ["inst-1"],
  }));
  const boxId = templateInstanceBoxNodeId("inst-1");

  /* Exactly one box, no members, no frame. */
  const box = graph.rfNodes.find((item) => item.id === boxId);
  assert.ok(box, "a collapsed instance must render its box");
  assert.equal(box.type, "templateInstanceBox");
  assert.equal(box.parentNode, `planspace:${TEMPLATE_LANE}`);
  assert.equal(graph.rfNodes.some((item) => item.type === "templateGroup"), false);
  for (const id of ["tpl-a", "tpl-b", "tpl-sink"]) {
    assert.equal(
      graph.rfNodes.some((item) => item.id === id),
      false,
      `${id} must be hidden while its instance is collapsed`,
    );
  }
  assert.equal(box.data.progress.total, 3);
  assert.deepEqual(box.data.sinkNodeIds, ["tpl-sink"]);
  assert.equal(graph.templateInstances["inst-1"].collapsed, true);

  /* Boundary-crossing edges land on the box; internal ones are dropped. */
  const edgeIds = graph.rfEdges.map((edge) => edge.id);
  assert.ok(edgeIds.includes(`dep:upstream->${boxId}`), "input binding reaches the box");
  assert.ok(edgeIds.includes(`dep:${boxId}->downstream`), "consumers leave the box");
  for (const edge of graph.rfEdges) {
    assert.notEqual(edge.source, edge.target, "no self-loop from a collapsed instance");
    for (const id of ["tpl-a", "tpl-b", "tpl-sink"]) {
      assert.notEqual(edge.source, id);
      assert.notEqual(edge.target, id);
    }
  }
  /* tpl-sink depended on both tpl-a and tpl-b; those two edges collapse onto
   * the same pair and must be de-duplicated rather than emitted twice. */
  assert.equal(new Set(edgeIds).size, edgeIds.length, "edge ids stay unique");

  /* A collapsed instance must not make its upstream look like a lane tail. */
  const upstream = graph.rfNodes.find((item) => item.id === "upstream");
  assert.equal(
    (upstream?.data as { isLastInLane?: boolean }).isLastInLane,
    false,
    "a node feeding a collapsed instance still has a descendant",
  );
}

function testCollapsedInstanceErrorAndProgressRollup(): void {
  const graph = buildGraph(templateArgs({
    nodes: instanceNodes().map((member) => {
      if (member.id === "tpl-a") return { ...member, state: "done" as const };
      if (member.id === "tpl-b") return { ...member, state: "error" as const };
      return member;
    }),
    collapsedTemplateInstanceIds: ["inst-1"],
  }));
  const box = graph.rfNodes.find(
    (item) => item.id === templateInstanceBoxNodeId("inst-1"),
  );
  assert.ok(box);
  assert.deepEqual(box.data.progress, {
    total: 3,
    done: 1,
    running: 0,
    hasError: true,
  });
}

function testCollapsedInstanceKeepsItsLaneAndHonoursHints(): void {
  /* A lane whose only nodes are inside a collapsed instance must keep its
   * swimlane, and the box must accept a manual position. */
  const dragged = { x: 640, y: 300 };
  const graph = buildGraph(templateArgs({
    collapsedTemplateInstanceIds: ["inst-1"],
    layoutHints: { [templateInstanceBoxNodeId("inst-1")]: dragged },
  }));
  const lane = graph.rfNodes.find(
    (item) => item.id === `planspace:${TEMPLATE_LANE}`,
  );
  assert.ok(lane, "the lane must survive collapsing every node inside it");
  const box = graph.rfNodes.find(
    (item) => item.id === templateInstanceBoxNodeId("inst-1"),
  );
  assert.deepEqual(box?.position, dragged);
  assert.ok(
    dragged.x + (box?.width ?? 0) + LANE.planspaceLanePaddingX <= (lane.width ?? 0),
    "the lane must grow to contain a dragged box",
  );

  /* A collapsed box consumes exactly one lane slot, and a dragged box advances
   * the cursor from where it actually sits — the same rule `nextLanePosition`
   * applies to a dragged tile. Asserted against the equivalent plain graph so
   * the two stay consistent rather than pinned to a literal. */
  const withLater = buildGraph(templateArgs({
    nodes: [
      ...instanceNodes(),
      node("after-box", { planspace_id: TEMPLATE_LANE, created_at: 9 }),
    ],
    collapsedTemplateInstanceIds: ["inst-1"],
    layoutHints: { [templateInstanceBoxNodeId("inst-1")]: dragged },
  }));
  const equivalentTile = buildGraph(args({
    nodes: [
      node("stand-in", { planspace_id: TEMPLATE_LANE, created_at: 1 }),
      node("after-box", { planspace_id: TEMPLATE_LANE, created_at: 9 }),
    ],
    knownPlanspaceIds: [TEMPLATE_LANE],
    layoutHints: { "stand-in": dragged },
  }));
  assert.deepEqual(
    withLater.rfNodes.find((item) => item.id === "after-box")?.position,
    equivalentTile.rfNodes.find((item) => item.id === "after-box")?.position,
    "a collapsed instance must occupy one lane slot, exactly like one tile",
  );
}

function testNonTemplateLayoutIsUnchangedByGroupSupport(): void {
  /* buildGraph is the common path for every canvas, so a graph with no
   * template nodes must be byte-identical whether or not instance inputs are
   * supplied. This is the guard against group support leaking into ordinary
   * layout. */
  const plainNodes = () => [
    node("first", { planspace_id: TEMPLATE_LANE, created_at: 1 }),
    node("second", {
      planspace_id: TEMPLATE_LANE,
      state: "virtual",
      scheduled_deps: ["first"],
      created_at: 2,
    }),
    node("failed", {
      planspace_id: TEMPLATE_LANE,
      state: "error",
      error: "boom",
      created_at: 3,
    }),
    node("rerun", {
      planspace_id: TEMPLATE_LANE,
      state: "queued",
      proposed_by: "rerun:failed",
      created_at: 4,
    }),
    node("free", { created_at: 5 }),
  ];
  const baseline = buildGraph(args({
    nodes: plainNodes(),
    knownPlanspaceIds: [TEMPLATE_LANE],
  }));
  const withInstanceInputs = buildGraph(args({
    nodes: plainNodes(),
    knownPlanspaceIds: [TEMPLATE_LANE],
    templateInstances: [instanceRecord()],
    collapsedTemplateInstanceIds: ["inst-1"],
  }));

  assert.deepEqual(
    withInstanceInputs.rfNodes.map((item) => ({
      id: item.id,
      type: item.type,
      position: item.position,
      width: item.width,
      height: item.height,
      parentNode: item.parentNode,
    })),
    baseline.rfNodes.map((item) => ({
      id: item.id,
      type: item.type,
      position: item.position,
      width: item.width,
      height: item.height,
      parentNode: item.parentNode,
    })),
    "instance inputs must not move any non-template node",
  );
  assert.deepEqual(
    withInstanceInputs.rfEdges.map((edge) => edge.id).sort(),
    baseline.rfEdges.map((edge) => edge.id).sort(),
    "instance inputs must not change edges for a template-free graph",
  );
  assert.deepEqual(baseline.templateInstances, {});
  assert.equal(
    baseline.rfNodes.some(
      (item) => item.type === "templateGroup" || item.type === "templateInstanceBox",
    ),
    false,
  );
}

function testInstanceSummaryHelpers(): void {
  assert.deepEqual(summarizeInstanceArguments(undefined), []);
  assert.deepEqual(
    summarizeInstanceArguments({
      ...instanceRecord(),
      arguments: { a: "1", b: "2", c: "3", d: "4" },
    }),
    [
      { name: "a", value: "1" },
      { name: "b", value: "2" },
      { name: "c", value: "3" },
    ],
    "the header shows at most three arguments",
  );
  assert.deepEqual(
    summarizeInstanceProgress(["x", "missing"], new Map([["x", node("x")]])),
    { total: 2, done: 1, running: 0, hasError: false },
    "an unknown member still counts toward the total",
  );
}

function testCollapsingKeepsTheInstanceInPlace(): void {
  /* Collapsing must not relocate the instance: the box claims the lane slot of
   * its first member rather than being appended after every other tile, so a
   * node that sat downstream of the instance stays downstream of the box. */
  const laneNodes = [
    node("before", { planspace_id: TEMPLATE_LANE, created_at: 0 }),
    ...instanceNodes(),
    node("after", { planspace_id: TEMPLATE_LANE, created_at: 4 }),
  ];
  const expanded = buildGraph(templateArgs({ nodes: laneNodes }));
  const collapsed = buildGraph(templateArgs({
    nodes: laneNodes,
    collapsedTemplateInstanceIds: ["inst-1"],
  }));

  const box = collapsed.rfNodes.find(
    (item) => item.id === templateInstanceBoxNodeId("inst-1"),
  );
  const frame = expanded.rfNodes.find((item) => item.type === "templateGroup");
  assert.ok(box);
  assert.ok(frame);
  /* The box occupies the frame's slot, not the first member's — the members sit
   * inset by the frame padding, so comparing against the frame is what keeps
   * expand/collapse from nudging the instance sideways. */
  assert.equal(
    box.position.x,
    frame.position.x,
    "the box takes the lane slot its group frame held",
  );

  const beforeX = (graph: ReturnType<typeof buildGraph>) =>
    graph.rfNodes.find((item) => item.id === "before")!.position.x;
  assert.equal(beforeX(collapsed), beforeX(expanded), "upstream tiles do not move");
  const afterCollapsed = collapsed.rfNodes.find((item) => item.id === "after")!;
  assert.ok(
    afterCollapsed.position.x > box.position.x,
    "a downstream tile stays to the right of the collapsed box",
  );
  /* Collapsing frees the slots the hidden members held, so the lane shrinks. */
  const laneWidth = (graph: ReturnType<typeof buildGraph>) =>
    graph.rfNodes.find((item) => item.id === `planspace:${TEMPLATE_LANE}`)!.width ?? 0;
  assert.ok(
    laneWidth(collapsed) < laneWidth(expanded),
    "a collapsed instance needs less lane width than its expanded members",
  );
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
testSameInstanceNodesClusterTogether();
testInstanceGroupDoesNotConsumeExtraLaneSlots();
testLayoutHintsOverrideInstanceClustering();
testInstanceClusteringSurvivesReversedNodeOrder();
testInstanceSpanningTwoLanesDegradesGracefully();
testInstanceGeometryFlowsIntoLaneSizing();
testSinkDetectionIgnoresExternalDownstream();
testCollapsedInstanceRendersOneBoxAndRedirectsEdges();
testCollapsedInstanceErrorAndProgressRollup();
testCollapsedInstanceKeepsItsLaneAndHonoursHints();
testCollapsingKeepsTheInstanceInPlace();
testNonTemplateLayoutIsUnchangedByGroupSupport();
testInstanceSummaryHelpers();
console.log("canvas layout tests passed");
