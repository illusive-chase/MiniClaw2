/* The long-press-to-wire gesture, shared between the tile that starts it and
 * the canvas that draws and completes it.
 *
 * The gesture begins on one agent tile's dependency button and ends anywhere on
 * the canvas, so neither side owns it alone: AgentNode knows which node was
 * pressed, Canvas knows the viewport transform, what is under the cursor, and
 * how to resolve a collapsed instance box back to a durable node id. React Flow
 * memoizes node components, so passing this through node `data` would mean
 * rebuilding the whole node array on every pointer move.
 *
 * Same module-singleton shape as `hoverStore` and `lanePlacement`, with the
 * pointer position kept out of React state entirely — see `subscribePointer`.
 */

import { useSyncExternalStore } from "react";

/** Where the wire currently ends, in screen coordinates. */
export type WiringPoint = { x: number; y: number };

export type WiringDrag = {
  /** The node the wire leaves — always a durable id, never an instance box. */
  sourceId: string;
  /** Screen coordinates of the button the press started on. */
  origin: WiringPoint;
};

/* Two subscriber sets, because the two halves of this state change at very
 * different rates. Whether a drag exists at all changes twice per gesture and
 * drives real re-renders (the canvas mounts an overlay, tiles show a drop
 * target). The cursor position changes on every pointer move and only ever
 * moves an SVG path, so it is published to an animation-frame reader instead of
 * through React state — routing it through `useState` would re-render the
 * canvas subtree dozens of times a second for one changed attribute. */
type Listener = () => void;

const dragListeners = new Set<Listener>();
const pointerListeners = new Set<Listener>();

let drag: WiringDrag | null = null;
let pointer: WiringPoint | null = null;
/** Set while the wire is over a node that would accept it, for the cursor and
 * the wire's own colour. Owned by the canvas, which does the hit-testing. */
let hoverTargetId: string | null = null;

export function subscribeWiringDrag(listener: Listener): () => void {
  dragListeners.add(listener);
  return () => dragListeners.delete(listener);
}

export function getWiringDragSnapshot(): WiringDrag | null {
  return drag;
}

/** Subscribe to cursor movement during a drag. Read with `getPointer` from
 * inside a render or an animation frame; this never triggers React updates. */
export function subscribeWiringPointer(listener: Listener): () => void {
  pointerListeners.add(listener);
  return () => pointerListeners.delete(listener);
}

export function getWiringPointer(): WiringPoint | null {
  return pointer;
}

export function getWiringHoverTargetId(): string | null {
  return hoverTargetId;
}

/** Begin a wire from `sourceId`. The origin doubles as the first pointer
 * position so the wire has a defined shape before the pointer moves. */
export function startWiringDrag(sourceId: string, origin: WiringPoint): void {
  drag = { sourceId, origin };
  pointer = origin;
  hoverTargetId = null;
  for (const listener of dragListeners) listener();
  for (const listener of pointerListeners) listener();
}

export function moveWiringPointer(
  next: WiringPoint,
  overTargetId: string | null,
): void {
  if (!drag) return;
  pointer = next;
  hoverTargetId = overTargetId;
  for (const listener of pointerListeners) listener();
}

export function endWiringDrag(): void {
  if (!drag) return;
  drag = null;
  pointer = null;
  hoverTargetId = null;
  for (const listener of dragListeners) listener();
  for (const listener of pointerListeners) listener();
}

/** Whether a wire is in flight. Drives the canvas overlay and the tiles'
 * drop-target affordance. */
export function useWiringDrag(): WiringDrag | null {
  return useSyncExternalStore(
    subscribeWiringDrag,
    getWiringDragSnapshot,
    getWiringDragSnapshot,
  );
}
