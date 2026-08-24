/* The wiring drag store: the handoff between the tile that starts a wire and
 * the canvas that draws and completes it.
 *
 * Worth testing directly because the two halves of the state are published
 * through separate subscriber sets on purpose — existence changes twice per
 * gesture and drives re-renders, position changes every pointer move and must
 * not. A regression that collapsed them would be invisible in types and only
 * show up as a stuttering canvas.
 */

import assert from "node:assert/strict";

import {
  endWiringDrag,
  getWiringDragSnapshot,
  getWiringHoverTargetId,
  getWiringPointer,
  moveWiringPointer,
  startWiringDrag,
  subscribeWiringDrag,
  subscribeWiringPointer,
} from "../src/canvas/wiringDragStore";

/* A counter per subscriber set, so each assertion can name which set fired. */
function counters() {
  const counts = { drag: 0, pointer: 0 };
  const offDrag = subscribeWiringDrag(() => {
    counts.drag += 1;
  });
  const offPointer = subscribeWiringPointer(() => {
    counts.pointer += 1;
  });
  return { counts, dispose: () => (offDrag(), offPointer()) };
}

/* At rest the store holds nothing, so the canvas mounts no overlay and
 * registers no pointer listeners. */
{
  assert.equal(getWiringDragSnapshot(), null);
  assert.equal(getWiringPointer(), null);
  assert.equal(getWiringHoverTargetId(), null);
}

/* A full gesture. The origin doubles as the first pointer position so the wire
 * has a defined shape before the pointer moves — without that the first frame
 * would draw from the button to (0,0). */
{
  const { counts, dispose } = counters();

  startWiringDrag("src", { x: 10, y: 20 });
  assert.deepEqual(getWiringDragSnapshot(), {
    sourceId: "src",
    origin: { x: 10, y: 20 },
  });
  assert.deepEqual(getWiringPointer(), { x: 10, y: 20 });
  assert.equal(counts.drag, 1, "existence changed once");

  moveWiringPointer({ x: 40, y: 60 }, "dst");
  assert.deepEqual(getWiringPointer(), { x: 40, y: 60 });
  assert.equal(getWiringHoverTargetId(), "dst");
  /* The decisive assertion: moving the cursor must NOT notify the existence
   * subscribers, or the canvas re-renders on every pointer move. */
  assert.equal(counts.drag, 1, "moving must not touch drag subscribers");
  assert.equal(counts.pointer, 2, "origin plus one move");

  endWiringDrag();
  assert.equal(getWiringDragSnapshot(), null);
  assert.equal(getWiringPointer(), null);
  assert.equal(getWiringHoverTargetId(), null, "hover target cleared with the drag");
  assert.equal(counts.drag, 2);

  dispose();
}

/* Moves outside a gesture are ignored. A pointerup can race the listener
 * teardown, and a stray move must not resurrect a wire that already landed. */
{
  const { counts, dispose } = counters();
  moveWiringPointer({ x: 5, y: 5 }, "dst");
  assert.equal(getWiringPointer(), null, "no wire, no position");
  assert.equal(getWiringHoverTargetId(), null);
  assert.equal(counts.pointer, 0, "ignored moves notify nobody");
  dispose();
}

/* Ending twice is harmless — pointerup and pointercancel can both arrive. Only
 * the first does anything, so the canvas does not run its drop logic twice. */
{
  const { counts, dispose } = counters();
  startWiringDrag("src", { x: 0, y: 0 });
  endWiringDrag();
  endWiringDrag();
  assert.equal(counts.drag, 2, "start plus one effective end");
  dispose();
}

/* A second gesture starts clean: no hover target carried over from the last
 * one, which would otherwise paint the wire solid before it is over anything. */
{
  startWiringDrag("a", { x: 1, y: 1 });
  moveWiringPointer({ x: 2, y: 2 }, "target");
  endWiringDrag();
  startWiringDrag("b", { x: 3, y: 3 });
  assert.equal(getWiringHoverTargetId(), null);
  assert.equal(getWiringDragSnapshot()?.sourceId, "b");
  endWiringDrag();
}

/* Unsubscribing actually detaches. The canvas subscribes per gesture, so a leak
 * here would accumulate listeners across every wire the user pulls. */
{
  let seen = 0;
  const off = subscribeWiringDrag(() => {
    seen += 1;
  });
  off();
  startWiringDrag("src", { x: 0, y: 0 });
  endWiringDrag();
  assert.equal(seen, 0);
}

console.log("wiring-drag-store: ok");
