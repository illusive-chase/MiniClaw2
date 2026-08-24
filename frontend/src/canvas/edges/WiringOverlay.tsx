/* The wire drawn while a dependency is being pulled out of a tile.
 *
 * Not React Flow's `connectionLineComponent`: that only exists during a drag
 * that started on a `<Handle>`, and this gesture starts on an ordinary button so
 * the wire is ours to draw. Rendered in screen coordinates over the canvas,
 * which keeps it correct while the pointer moves without re-running layout.
 *
 * The pointer position is read through an animation frame rather than React
 * state — one changed path attribute per frame should not re-render the canvas.
 */

import { useEffect, useRef } from "react";
import {
  getWiringHoverTargetId,
  getWiringPointer,
  subscribeWiringPointer,
  type WiringDrag,
} from "../wiringDragStore";

/** Bezier with horizontal control points, matching DependencyEdge's shape so
 * the wire does not visibly jump when the drop turns it into a real edge. */
function wirePath(
  from: { x: number; y: number },
  to: { x: number; y: number },
): string {
  const dx = Math.abs(to.x - from.x);
  const control = Math.max(24, dx * 0.24 + 24);
  return `M${from.x},${from.y} C${from.x + control},${from.y} ${to.x - control},${to.y} ${to.x},${to.y}`;
}

export function WiringOverlay({ drag }: { drag: WiringDrag }) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const pathRef = useRef<SVGPathElement | null>(null);
  const dotRef = useRef<SVGCircleElement | null>(null);
  const groupRef = useRef<SVGGElement | null>(null);

  useEffect(() => {
    let frame = 0;
    /* Coalesce to one write per frame: pointermove can outpace paint, and every
     * event would otherwise re-read the same geometry. */
    const paint = () => {
      frame = 0;
      const svg = svgRef.current;
      if (!svg) return;
      /* The store holds viewport coordinates, because that is what pointer
       * events report and what the button's own position is measured in. This
       * SVG's coordinate space starts at the canvas wrapper's top-left, which
       * is offset by whatever chrome sits above and left of it, so both ends of
       * the wire are rebased here. Re-read per frame rather than cached: the
       * canvas can be resized or scrolled mid-gesture. */
      const box = svg.getBoundingClientRect();
      const rebase = (point: { x: number; y: number }) => ({
        x: point.x - box.left,
        y: point.y - box.top,
      });
      const from = rebase(drag.origin);
      const to = rebase(getWiringPointer() ?? drag.origin);
      pathRef.current?.setAttribute("d", wirePath(from, to));
      dotRef.current?.setAttribute("cx", String(to.x));
      dotRef.current?.setAttribute("cy", String(to.y));
      /* Over a legal target the wire commits to solid brand; over empty canvas
       * it stays dashed, which is what "release here and I make a node" looks
       * like next to a settled edge. */
      const over = getWiringHoverTargetId() !== null;
      groupRef.current?.setAttribute("data-over", over ? "true" : "false");
    };
    paint();
    const unsubscribe = subscribeWiringPointer(() => {
      if (frame === 0) frame = window.requestAnimationFrame(paint);
    });
    return () => {
      unsubscribe();
      if (frame !== 0) window.cancelAnimationFrame(frame);
    };
  }, [drag]);

  return (
    <svg
      ref={svgRef}
      className="pointer-events-none absolute inset-0 z-30 h-full w-full overflow-visible"
      aria-hidden="true"
    >
      <g ref={groupRef} className="wiring-overlay">
        <path
          ref={pathRef}
          fill="none"
          stroke="rgb(var(--brand))"
          strokeWidth={1.9}
          strokeLinecap="round"
          opacity={0.95}
        />
        <circle
          ref={dotRef}
          r={3.5}
          fill="rgb(var(--brand))"
          stroke="none"
        />
      </g>
    </svg>
  );
}
