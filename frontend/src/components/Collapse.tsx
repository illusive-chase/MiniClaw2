import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { EXIT_MS, usePresence } from "../motion";

/**
 * Height expand/collapse without measuring: a one-row grid whose track goes
 * `0fr → 1fr`. The body stays mounted only while open or closing, so a
 * collapsed section costs nothing to render.
 *
 * Kept off the canvas: animating a grid track re-runs layout every frame,
 * which is fine in a panel and not fine among a hundred tiles.
 */
export function Collapse({
  open,
  children,
  className = "",
}: {
  open: boolean;
  children: ReactNode;
  className?: string;
}) {
  const { mounted } = usePresence(open, EXIT_MS.panel);
  /* A section that is already open when it first mounts (page load, a
   * remount on scroll) is not news; only later toggles animate. */
  const openAtMount = useRef(open);
  const toggled = useRef(false);
  if (open !== openAtMount.current) toggled.current = true;
  if (!mounted) return null;
  return (
    <CollapseFrame open={open} instant={!toggled.current} className={className}>
      {children}
    </CollapseFrame>
  );
}

function CollapseFrame({
  open,
  instant,
  className,
  children,
}: {
  open: boolean;
  instant: boolean;
  className: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  /* The frame mounts closed. Before the first paint the closed track is
   * forced into computed style, then the frame flips open — so the browser
   * has a 0fr starting point to transition from instead of appearing at full
   * height. */
  const [expanded, setExpanded] = useState(instant && open);
  useLayoutEffect(() => {
    if (!open) {
      setExpanded(false);
      return;
    }
    void ref.current?.offsetHeight;
    setExpanded(true);
  }, [open]);
  return (
    <div ref={ref} className="mc-collapse" data-open={expanded} data-instant={instant}>
      <div className={`mc-collapse-inner ${className}`}>{children}</div>
    </div>
  );
}
