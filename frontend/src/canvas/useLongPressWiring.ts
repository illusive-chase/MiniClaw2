/* Long-press on the dependency button, held to pull out a wire.
 *
 * One button, two gestures. A plain click keeps its existing meaning — create a
 * downstream virtual attached to this node — and holding the same button starts
 * a wire the user aims at whatever the new dependency should join. The two must
 * not both fire from one press, so the click is suppressed once the wire is out.
 *
 * The hold is resolved by whichever comes first: the timer, or the pointer
 * moving past a few pixels. Waiting only for the timer would make a quick
 * drag-away feel dead; the movement threshold also keeps a shaky press from
 * cancelling itself.
 */

import { useCallback, useEffect, useRef } from "react";

/** How long the button must be held before a wire appears. Long enough not to
 * fire on an ordinary click, short enough that the hold does not feel stuck. */
export const WIRING_PRESS_DELAY_MS = 220;
/** Pointer travel that starts the wire before the timer does. */
export const WIRING_PRESS_SLOP_PX = 5;

export type LongPressWiringHandlers = {
  onPointerDown: (event: React.PointerEvent) => void;
  onClickCapture: (event: React.MouseEvent) => void;
};

/**
 * `onBegin` receives the screen position the press started at, which becomes
 * the wire's anchor. `enabled` false leaves the button a plain click.
 */
export function useLongPressWiring({
  enabled,
  onBegin,
}: {
  enabled: boolean;
  onBegin: (origin: { x: number; y: number }) => void;
}): LongPressWiringHandlers {
  const timerRef = useRef<number | null>(null);
  /* Set the moment a wire starts and read by the click handler that fires
   * afterwards on the same press, so the hold does not also create a node. */
  const startedRef = useRef(false);
  /* Tears down one press's window listeners. Held in a ref so unmounting
   * mid-press cleans up too: a tile can disappear under the user's finger when
   * an upstream update collapses a template instance or the lane rebuilds. */
  const disposeRef = useRef<(() => void) | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const dispose = useCallback(() => {
    clearTimer();
    disposeRef.current?.();
    disposeRef.current = null;
  }, [clearTimer]);

  useEffect(() => dispose, [dispose]);

  const onPointerDown = useCallback(
    (event: React.PointerEvent) => {
      if (!enabled || event.button !== 0) return;
      /* The tile below must not start dragging, and the canvas must not treat
       * this as the beginning of a marquee. */
      event.stopPropagation();
      /* A previous press that never saw its release (pointer capture lost, or a
       * re-entrant press) must not leave listeners behind. */
      dispose();
      startedRef.current = false;
      const origin = { x: event.clientX, y: event.clientY };

      const detach = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
      };
      const begin = () => {
        if (startedRef.current) return;
        startedRef.current = true;
        dispose();
        onBegin(origin);
      };
      /* A press that travels before the timer fires is already a drag. */
      const onMove = (moveEvent: PointerEvent) => {
        const dx = moveEvent.clientX - origin.x;
        const dy = moveEvent.clientY - origin.y;
        if (Math.hypot(dx, dy) >= WIRING_PRESS_SLOP_PX) begin();
      };
      /* Released before either threshold: an ordinary click, which the button's
       * own onClick handles. Nothing to undo. */
      const onUp = () => {
        dispose();
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
      disposeRef.current = detach;
      timerRef.current = window.setTimeout(begin, WIRING_PRESS_DELAY_MS);
    },
    [dispose, enabled, onBegin],
  );

  /* Capture phase, so the suppression runs before the button's own onClick. */
  const onClickCapture = useCallback((event: React.MouseEvent) => {
    if (!startedRef.current) return;
    startedRef.current = false;
    event.preventDefault();
    event.stopPropagation();
  }, []);

  return { onPointerDown, onClickCapture };
}
