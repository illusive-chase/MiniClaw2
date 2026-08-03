import assert from "node:assert/strict";
import { decorateEdges, resolveHoverGroup } from "../src/canvas/edgeVisibility";
import {
  decoratePendingGateLayers,
  PENDING_GATE_NODE_Z_INDEX,
} from "../src/canvas/nodeLayers";
import {
  buildGraph,
  classifyPlanspaceLaneResizes,
  contextIdentityKey,
  LANE,
  resizePlanspaceLanes,
  type BuildGraphArgs,
} from "../src/canvas/layout";
import type {
  CommitDescriptor,
  ContextBundle,
  NodeInfo,
} from "../src/types";

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

function commit(sha: string, externalCount = 0): CommitDescriptor {
  return {
    sha,
    live: true,
    message: sha,
    external_count_before: externalCount,
    aliases: [],
  };
}

function args(overrides: Partial<BuildGraphArgs> = {}): BuildGraphArgs {
  return {
    nodes: [],
    activeNodeIds: [],
    layoutHints: {},
    contextBundlesByNodeId: {},
    knownPlanspaceIds: [],
    hiddenPlanspaceIds: [],
    activePlanspaceId: null,
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
    lanes(populated).slice(0, 2).map((item) => item.position),
    lanes(empty).map((item) => item.position),
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
  /* Loads run tile-left → agent-top; ops keep the default anchors. */
  assert.equal(
    declaredLoads.every((edge) => edge.sourceHandle === "loads" && edge.targetHandle === "loads"),
    true,
  );
  const opLoad = buildGraph(args({
    nodes: [node("shell", { kind: "op", state: "running" })],
    contextBundlesByNodeId: {
      shell: bundle("shell", "principle", `${principle.path}/CONTEXT.md`, principle.id),
    },
  })).rfEdges.find((edge) => edge.type === "loads");
  assert.equal(opLoad?.targetHandle, undefined);

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
    new Set(["agent", "op", "errorTerminal", "artifact", "context"]),
  );
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
  assert.ok(originalFirst);
  assert.ok(originalSecond);
  assert.ok(originalThird);

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
  assert.equal(grownThird.position.y, grownFirst.position.y + (grownFirst.height ?? 0) + 40);

  const restoredChild = grown.map((item) =>
    item.id === "work"
      ? {
          ...item,
          position: { ...item.position, y: LANE.planspaceLaneAgentRowY },
          height: LANE.agentHeight,
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
testProjectScopedLaneLabelShowsOnlyDirectionName();
testVerticalCommitTrunkAndStableLaneX();
testEpochLinksAndHoverGroups();
testBindingDrivenContextTiles();
testFloatingContextDoesNotOverlapFirstLane();
testPlanspaceChildrenHaveOneSidedExtent();
testPlanspaceLaneLiveGrowthAndDropFit();
testPlanspaceLaneResizeReflowsLaterAutomaticLanes();
testObservedSkillMetadataEnrichment();
testEdgeWeights();
testPendingGateNodeLayer();
console.log("canvas layout tests passed");
