import type { ReactNode, RefObject } from "react";

import { EXIT_MS, usePresence } from "../../motion";

/**
 * Folder or archive panel over a blurred home grid. Shares the grid's cell
 * component, so a folder's members jiggle and drag exactly like the root.
 */
export function HomeOverlay({
  open,
  title,
  subtitle,
  footer,
  panelRef,
  leaving,
  rightInset,
  onBackdrop,
  children,
}: {
  open: boolean;
  title: string;
  subtitle: string;
  footer: string;
  panelRef: RefObject<HTMLDivElement>;
  leaving: boolean;
  rightInset: number;
  onBackdrop: () => void;
  children: ReactNode;
}) {
  const { mounted, phase } = usePresence(open, EXIT_MS.overlay);
  if (!mounted) return null;
  return (
    <div
      className="home-overlay absolute inset-y-0 left-0 z-[35] grid place-items-center bg-surface/55 p-8 backdrop-blur-[14px] backdrop-saturate-[.9] transition-[right] duration-[240ms]"
      style={{ right: rightInset }}
      data-phase={phase}
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onBackdrop();
      }}
    >
      <div className="w-[min(560px,100%)]" onPointerDown={(event) => {
        if (event.target === event.currentTarget) onBackdrop();
      }}>
        <div className="mb-4 text-center font-display text-[24px] font-semibold tracking-tight text-ink-strong">
          {title}
          <small className="mt-0.5 block font-sans text-[12px] font-normal text-ink-muted">{subtitle}</small>
        </div>
        <div
          ref={panelRef}
          data-leaving={leaving ? "true" : undefined}
          className="home-overlay-panel relative max-h-[min(560px,calc(100vh-220px))] overflow-y-auto rounded-[28px] bg-surface-raised/90 px-[18px] py-[22px] shadow-[0_0_0_1px_rgb(var(--border)),0_24px_60px_rgb(var(--shadow)/0.16)] transition-shadow duration-[200ms]"
        >
          {children}
        </div>
        <div className="mt-3 text-center text-[11.5px] text-ink-subtle">{footer}</div>
      </div>
    </div>
  );
}
