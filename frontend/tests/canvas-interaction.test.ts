import assert from "node:assert/strict";

import {
  INTERACTIVE_EDGE_CLASS,
  decorateEdges,
  resolveInteractiveDependencyEdges,
} from "../src/canvas/edgeVisibility";
import {
  RIGHT_DRAG_PAN_SLOP_PX,
  isRightDragPan,
  panViewportBy,
  shouldPanThroughRightDrag,
} from "../src/canvas/rightDragPan";
import type { RFEdge } from "../src/canvas/layout";

const identity = (renderId: string) => renderId;

function dep(id: string, source: string, target: string): RFEdge {
  return { id, source, target, type: "dependency" };
}

/* ─────────── which arrows may claim a pointer hit ─────────── */

/* An arrow is inert unless one of its endpoints is selected. This is the rule
 * that keeps right-drag from dying on strips of canvas along arrows nothing can
 * act on. */
function testUnselectedDependencyIsInert(): void {
  const edges = [dep("a->b", "a", "b")];
  const interactive = resolveInteractiveDependencyEdges({
    edges,
    selectedRenderIds: new Set<string>(),
    resolveConnectableNodeId: identity,
    canWithdraw: () => true,
  });
  assert.equal(interactive.size, 0);
}

/* Selecting either endpoint is enough — the user may have selected the upstream
 * node or the downstream one. */
function testEitherSelectedEndpointArmsTheEdge(): void {
  const edges = [dep("a->b", "a", "b")];
  for (const selected of ["a", "b"]) {
    const interactive = resolveInteractiveDependencyEdges({
      edges,
      selectedRenderIds: new Set([selected]),
      resolveConnectableNodeId: identity,
      canWithdraw: () => true,
    });
    assert.deepEqual([...interactive], ["a->b"]);
  }
}

/* Selection alone is not enough: an arrow that cannot be withdrawn (an executed
 * target, a read-only project) stays inert, because clicking it would do
 * nothing and the hit ribbon would only cost panning. */
function testSelectedButUnwithdrawableIsInert(): void {
  const interactive = resolveInteractiveDependencyEdges({
    edges: [dep("a->b", "a", "b")],
    selectedRenderIds: new Set(["a"]),
    resolveConnectableNodeId: identity,
    canWithdraw: () => false,
  });
  assert.equal(interactive.size, 0);
}

/* Only dependency arrows are ever actionable; timeline/resume/loads carry no
 * withdraw gesture even when their endpoints are selected. */
function testNonDependencyEdgesNeverArm(): void {
  const edges: RFEdge[] = [
    { id: "t", source: "a", target: "b", type: "timeline" },
    { id: "r", source: "a", target: "b", type: "resume" },
    { id: "l", source: "ctx", target: "a", type: "loads" },
  ];
  const interactive = resolveInteractiveDependencyEdges({
    edges,
    selectedRenderIds: new Set(["a", "b", "ctx"]),
    resolveConnectableNodeId: identity,
    canWithdraw: () => true,
  });
  assert.equal(interactive.size, 0);
}

/* An edge already showing its withdraw control keeps the hit ribbon even if the
 * selection moved away, so the affordance cannot be stranded unclickable. */
function testOpenDisconnectControlKeepsItsRibbon(): void {
  const interactive = resolveInteractiveDependencyEdges({
    edges: [dep("a->b", "a", "b")],
    selectedRenderIds: new Set<string>(),
    resolveConnectableNodeId: identity,
    canWithdraw: () => false,
    keepEdgeId: "a->b",
  });
  assert.deepEqual([...interactive], ["a->b"]);
}

/* A collapsed template instance renders as one box, so the endpoint the array
 * would actually rewrite differs from the rendered id. An endpoint that does not
 * resolve (an instance used as a target) cannot be withdrawn. */
function testUnresolvableEndpointIsInert(): void {
  const interactive = resolveInteractiveDependencyEdges({
    edges: [dep("box->b", "box", "b")],
    selectedRenderIds: new Set(["box"]),
    resolveConnectableNodeId: (renderId, role) =>
      renderId === "box" ? (role === "source" ? "member" : null) : renderId,
    canWithdraw: () => true,
  });
  assert.deepEqual([...interactive], ["box->b"]);

  const asTarget = resolveInteractiveDependencyEdges({
    edges: [dep("a->box", "a", "box")],
    selectedRenderIds: new Set(["a"]),
    resolveConnectableNodeId: (renderId, role) =>
      renderId === "box" ? (role === "source" ? "member" : null) : renderId,
    canWithdraw: () => true,
  });
  assert.equal(asTarget.size, 0);
}

/* The withdraw gate is asked about the resolved durable pair, not the rendered
 * ids — otherwise a collapsed instance would be judged on a box id that owns no
 * dependency array. */
function testWithdrawGateSeesResolvedIds(): void {
  const seen: Array<[string, string]> = [];
  resolveInteractiveDependencyEdges({
    edges: [dep("box->b", "box", "b")],
    selectedRenderIds: new Set(["b"]),
    resolveConnectableNodeId: (renderId) =>
      renderId === "box" ? "member" : renderId,
    canWithdraw: (sourceId, targetId) => {
      seen.push([sourceId, targetId]);
      return true;
    },
  });
  assert.deepEqual(seen, [["member", "b"]]);
}

/* ─────────── the class the CSS rule keys on ─────────── */

/* Only armed edges carry the class, and the flag is restated on every pass so a
 * stale class cannot outlive the selection that earned it. */
function testInteractiveClassTracksTheArmedSet(): void {
  const edges = [dep("armed", "a", "b"), dep("inert", "c", "d")];
  const decorated = decorateEdges(edges, null, [], null, new Set(["armed"]));
  assert.equal(
    decorated.find((edge) => edge.id === "armed")?.className,
    INTERACTIVE_EDGE_CLASS,
  );
  assert.equal(decorated.find((edge) => edge.id === "inert")?.className, undefined);

  /* Re-decorating what a previous pass armed must clear the class. */
  const rearmed = decorateEdges(decorated as RFEdge[], null, [], null, new Set());
  assert.equal(rearmed.find((edge) => edge.id === "armed")?.className, undefined);
}

/* The class survives the other decorations, which return fresh objects: a
 * selected endpoint, and the faded context lanes. */
function testInteractiveClassSurvivesOtherDecorations(): void {
  const selected = decorateEdges(
    [dep("armed", "a", "b")],
    "a",
    [],
    null,
    new Set(["armed"]),
  );
  assert.equal(selected[0].className, INTERACTIVE_EDGE_CLASS);
  assert.equal(selected[0].selected, true);

  const withControl = decorateEdges(
    [dep("armed", "a", "b")],
    null,
    [],
    {
      edgeId: "armed",
      confirming: false,
      onRequest: () => {},
      onConfirm: () => {},
      onCancel: () => {},
    },
    new Set(["armed"]),
  );
  assert.equal(withControl[0].className, INTERACTIVE_EDGE_CLASS);
  assert.ok(withControl[0].data?.disconnect);
}

/* Omitting the set entirely leaves every edge unclassed, so callers that do not
 * arm anything are unaffected. */
function testNoArmedSetLeavesEdgesUnclassed(): void {
  const decorated = decorateEdges([dep("a->b", "a", "b")], null, []);
  assert.equal(decorated[0].className, undefined);
}

/* ─────────── right-drag pan-through ─────────── */

const hit = (over: Partial<Parameters<typeof shouldPanThroughRightDrag>[0]> = {}) => ({
  insideNoPan: true,
  insideSelectedNode: false,
  insideSelectionRect: false,
  insideEditable: false,
  ...over,
});

/* The case the whole mechanism exists for: a press on an unselected tile or an
 * armed arrow, which React Flow would refuse to pan from. */
function testPressOnNoPanElementPansThrough(): void {
  assert.equal(shouldPanThroughRightDrag(hit()), true);
}

/* Empty canvas is React Flow's own gesture; the canvas must not run a second
 * pan on top of it. */
function testPressOutsideNoPanIsLeftToReactFlow(): void {
  assert.equal(shouldPanThroughRightDrag(hit({ insideNoPan: false })), false);
}

/* A selected tile and the marquee rect both carry the save-as-template menu, so
 * their presses are menu gestures rather than pans. */
function testSelectedTargetsKeepTheirMenu(): void {
  assert.equal(shouldPanThroughRightDrag(hit({ insideSelectedNode: true })), false);
  assert.equal(shouldPanThroughRightDrag(hit({ insideSelectionRect: true })), false);
}

/* A text field keeps its native menu even unselected — panning would steal the
 * one right-click that still has a real use. */
function testEditableTargetsAreNeverPanned(): void {
  assert.equal(shouldPanThroughRightDrag(hit({ insideEditable: true })), false);
  assert.equal(
    shouldPanThroughRightDrag(hit({ insideEditable: true, insideSelectedNode: true })),
    false,
  );
}

/* Pan is pure translation: zoom must come through untouched, or the gesture
 * would rescale the graph as it moved. */
function testPanTranslatesWithoutRescaling(): void {
  assert.deepEqual(panViewportBy({ x: 10, y: 20, zoom: 0.75 }, 5, -8), {
    x: 15,
    y: 12,
    zoom: 0.75,
  });
}

/* Below the slop a press is still a click, so a menu can open on release; past
 * it the gesture is a drag and any menu would be unwanted. */
function testSlopSeparatesClickFromDrag(): void {
  assert.equal(isRightDragPan(0, 0), false);
  assert.equal(isRightDragPan(RIGHT_DRAG_PAN_SLOP_PX - 0.01, 0), false);
  assert.equal(isRightDragPan(RIGHT_DRAG_PAN_SLOP_PX, 0), true);
  /* Diagonal travel counts as its true distance, not per-axis. */
  assert.equal(isRightDragPan(3, 4), true);
}

testUnselectedDependencyIsInert();
testEitherSelectedEndpointArmsTheEdge();
testSelectedButUnwithdrawableIsInert();
testNonDependencyEdgesNeverArm();
testOpenDisconnectControlKeepsItsRibbon();
testUnresolvableEndpointIsInert();
testWithdrawGateSeesResolvedIds();
testInteractiveClassTracksTheArmedSet();
testInteractiveClassSurvivesOtherDecorations();
testNoArmedSetLeavesEdgesUnclassed();
testPressOnNoPanElementPansThrough();
testPressOutsideNoPanIsLeftToReactFlow();
testSelectedTargetsKeepTheirMenu();
testEditableTargetsAreNeverPanned();
testPanTranslatesWithoutRescaling();
testSlopSeparatesClickFromDrag();

console.log("canvas-interaction tests passed");
