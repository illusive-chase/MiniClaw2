/* Right-drag panning over elements React Flow refuses to pan on.
 *
 * This canvas pans on right-drag (`panOnDrag={[2]}`), which React Flow routes
 * through d3-zoom. d3's filter drops any press whose target sits inside an
 * element carrying `nopan` — and React Flow stamps `nopan` on every draggable
 * node wrapper, every edge `<g>`, and the multi-selection rect, unconditionally.
 * So a press that lands on a tile or an arrow is not a pan that gets cancelled;
 * it is a pan that never starts, and the canvas simply does not move.
 *
 * Narrowing what claims pointer events (see `resolveInteractiveDependencyEdges`)
 * removes the arrows nothing can act on, but tiles are large and must stay
 * clickable, so the dead area cannot be styled away. Instead the canvas runs the
 * pan itself for exactly the presses d3 declines, which is what these rules
 * decide. The gesture is hand-rolled the same way ctrl+wheel zoom already is,
 * over the same viewport plumbing.
 *
 * Kept free of DOM and React Flow types so the rules can be tested as functions;
 * the caller resolves each flag from the event target.
 */

/** What a right-button press landed on, as far as these rules care. */
export type RightDragHit = {
  /** The press is inside an element d3-zoom refuses to pan from. When false the
   * canvas leaves the gesture to React Flow, which already handles it. */
  insideNoPan: boolean;
  /** The press is inside a node tile the user has selected — right-clicking one
   * opens the node menu, so the press is a menu gesture, not a pan. */
  insideSelectedNode: boolean;
  /** The press is on the marquee selection rect, which carries the same menu. */
  insideSelectionRect: boolean;
  /** A text field or `contenteditable`. Panning would steal the one press that
   * still needs its native menu, so these are always left alone. */
  insideEditable: boolean;
};

/** Whether the canvas should run the pan itself for this press. */
export function shouldPanThroughRightDrag(hit: RightDragHit): boolean {
  if (!hit.insideNoPan) return false;
  if (hit.insideEditable) return false;
  if (hit.insideSelectedNode || hit.insideSelectionRect) return false;
  return true;
}

/** Translate a viewport by a screen-space drag delta.
 *
 * Pan is pure translation: the viewport transform translates before it scales,
 * so a screen delta moves `x`/`y` one-for-one and never touches `zoom`. */
export function panViewportBy(
  viewport: { x: number; y: number; zoom: number },
  dx: number,
  dy: number,
): { x: number; y: number; zoom: number } {
  return { x: viewport.x + dx, y: viewport.y + dy, zoom: viewport.zoom };
}

/** Travel past which a right-drag counts as a pan rather than a click. Below it
 * the gesture is treated as a stationary right-click so a menu can still open on
 * release; above it the press was a drag and any menu would be unwanted. */
export const RIGHT_DRAG_PAN_SLOP_PX = 3;

/** Whether a press that has travelled this far is a drag. */
export function isRightDragPan(dx: number, dy: number): boolean {
  return Math.hypot(dx, dy) >= RIGHT_DRAG_PAN_SLOP_PX;
}
