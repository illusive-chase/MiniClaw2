/* FLIP for the home grid: every slot that moves between two commits glides
 * from where it was drawn to where layout now puts it.
 *
 * This is what keeps the grid from jumping after any arrangement change —
 * drag reorder, drop, merge, pull-out, dissolve, keyboard move, a project
 * arriving or leaving, a window resize re-wrapping columns. Positions are kept
 * relative to the grid element, so scrolling and the sidebar recentring the
 * whole grid are not mistaken for a slot moving.
 *
 * A slot interrupted mid-glide restarts from its current drawn position rather
 * than snapping: the in-flight offset is read off before the old animation is
 * cancelled and folded into the new start point.
 */
import { useLayoutEffect, useRef, type RefObject } from "react";

import { prefersReducedMotion } from "../../motion";

const MOVE_MS = 300;
const MOVE_EASE = "cubic-bezier(0.22, 1, 0.36, 1)";
const ENTER_MS = 320;
const ENTER_EASE = "cubic-bezier(0.34, 1.36, 0.64, 1)";

type Point = { x: number; y: number };

export type FlipHandle = {
  /** Where layout puts `slot` right now, in viewport coordinates, ignoring
   * any glide in flight — the point a dropped ghost should land on. */
  layoutRect: (slot: string) => DOMRect | null;
};

export function useFlip(gridRef: RefObject<HTMLElement>, enabled = true): FlipHandle {
  const positions = useRef(new Map<string, Point>());
  const running = useRef(new Map<string, Animation>());
  const primed = useRef(false);

  useLayoutEffect(() => {
    const grid = gridRef.current;
    if (!grid) {
      positions.current = new Map();
      primed.current = false;
      return;
    }
    const reduced = prefersReducedMotion();
    const origin = grid.getBoundingClientRect();
    const next = new Map<string, Point>();
    const cells = grid.querySelectorAll<HTMLElement>(":scope > [data-slot]");
    for (const cell of cells) {
      const slot = cell.dataset.slot!;
      const drawn = cell.getBoundingClientRect();
      const glide = running.current.get(slot);
      // The offset a glide in flight is currently drawing at; layout is under it.
      let offset: Point = { x: 0, y: 0 };
      if (glide) {
        const matrix = new DOMMatrixReadOnly(getComputedStyle(cell).transform);
        offset = { x: matrix.m41, y: matrix.m42 };
      }
      const here = { x: drawn.left - offset.x - origin.left, y: drawn.top - offset.y - origin.top };
      next.set(slot, here);
      const before = positions.current.get(slot);
      if (before && Math.abs(before.x - here.x) < 0.5 && Math.abs(before.y - here.y) < 0.5) {
        // Same target as last commit: leave any glide in flight alone.
        continue;
      }
      if (glide) {
        glide.cancel();
        running.current.delete(slot);
      }
      if (!enabled || reduced) continue;
      if (!before) {
        if (primed.current) {
          cell.querySelector<HTMLElement>(".home-pop")?.animate(
            [
              { opacity: 0, transform: "scale(0.92)" },
              { opacity: 1, offset: 0.55 },
              { opacity: 1, transform: "none" },
            ],
            { duration: ENTER_MS, easing: ENTER_EASE },
          );
        }
        continue;
      }
      // Start from where the user last saw it: the old slot plus any glide offset.
      const dx = before.x + offset.x - here.x;
      const dy = before.y + offset.y - here.y;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
      const next_glide = cell.animate(
        [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "translate(0, 0)" }],
        { duration: MOVE_MS, easing: MOVE_EASE },
      );
      running.current.set(slot, next_glide);
      next_glide.onfinish = () => {
        if (running.current.get(slot) === next_glide) running.current.delete(slot);
      };
    }
    for (const slot of running.current.keys()) {
      if (!next.has(slot)) running.current.delete(slot);
    }
    positions.current = next;
    /* Arrivals pop only after the grid first showed something, so the initial
     * load and a refetch into an empty grid stay still. */
    primed.current = primed.current || cells.length > 0;
  });

  return {
    layoutRect(slot: string) {
      const grid = gridRef.current;
      const point = positions.current.get(slot);
      if (!grid || !point) return null;
      const origin = grid.getBoundingClientRect();
      const cell = grid.querySelector<HTMLElement>(`:scope > [data-slot="${CSS.escape(slot)}"]`);
      const size = cell?.getBoundingClientRect();
      return new DOMRect(origin.left + point.x, origin.top + point.y, size?.width ?? 0, size?.height ?? 0);
    },
  };
}
