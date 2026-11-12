import { useStore } from "reactflow";
import { EXIT_MS, useLatest, usePresence } from "../motion";
import { usePendingCreate, type PendingCreate } from "./nodeMotionStore";

/* An agent tile's footprint in flow units; scaled by the zoom so the
 * placeholder covers about where the real tile will land (its top-left at
 * the click, matching how the position is snapped). */
const PLACEHOLDER_WIDTH = 224;
const PLACEHOLDER_HEIGHT = 86;

/**
 * Immediate answer to "make a node here": a ripple from the click point and a
 * dashed placeholder, shown while the create request is in flight. Drawn over
 * React Flow in screen space — like `WiringOverlay` — so it never enters the
 * node list or the layout. The real tile replaces it with its own birth.
 */
export function PendingCreateOverlay({
  wrapperRef,
}: {
  wrapperRef: React.RefObject<HTMLDivElement>;
}) {
  const pending = usePendingCreate();
  const zoom = useStore((state) => state.transform[2]);
  const { mounted, phase } = usePresence(pending !== null, EXIT_MS.menu);
  const shown = useLatest<PendingCreate>(pending);
  const wrapper = wrapperRef.current;
  if (!mounted || !shown || !wrapper) return null;
  const rect = wrapper.getBoundingClientRect();
  const x = shown.x - rect.left;
  const y = shown.y - rect.top;
  const width = PLACEHOLDER_WIDTH * zoom;
  const height = PLACEHOLDER_HEIGHT * zoom;
  return (
    <div className="pointer-events-none absolute inset-0 z-10 overflow-hidden" aria-hidden="true">
      <span key={`r${shown.seq}`} className="drop-ripple" style={{ left: x, top: y }} />
      <span
        key={`p${shown.seq}`}
        data-phase={phase}
        className="drop-placeholder"
        style={{
          left: x + width / 2,
          top: y + height / 2,
          width,
          height,
        }}
      />
    </div>
  );
}
