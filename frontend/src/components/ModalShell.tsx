import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { EXIT_MS, usePresence } from "../motion";

/**
 * Open modals, innermost last. Only the top one answers Escape, so a text
 * zoom opened over a settings dialog closes alone.
 */
const modalStack: string[] = [];

type Props = {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  /** Accessible name; use `labelledBy` instead when a visible heading exists. */
  label?: string;
  labelledBy?: string;
  /** False while a submit is in flight: Escape and the scrim do nothing. */
  dismissible?: boolean;
  /** Read-only previews close on a scrim click; forms default to not, so a
   * stray click outside never discards what was typed. */
  closeOnScrim?: boolean;
  /** Classes for the panel box (size, radius, layout). */
  panelClassName: string;
  /** Stacking class for the scrim. */
  zClassName?: string;
  /** Extra scrim classes (padding, blur). */
  scrimClassName?: string;
  /** Full-screen surfaces settle in by scale only; there is nowhere to rise from. */
  variant?: "default" | "full";
  /** Fired once the exit animation has finished and the panel unmounted. */
  onExited?: () => void;
};

/**
 * The one modal frame: portal, scrim, entry and exit motion, Escape, focus.
 *
 * Portalled to `document.body` because a modal may be opened from inside the
 * side panel, and that panel slides with `transform` — which would make it the
 * containing block for this `position: fixed` scrim and pin it to the 380px
 * column instead of the viewport.
 *
 * Kept mounted for `EXIT_MS.modal` after `open` turns false so the panel can
 * leave; during that window the scrim takes no pointer events, so the click
 * that follows the close lands on whatever is underneath.
 */
export function ModalShell({
  open,
  onClose,
  children,
  label,
  labelledBy,
  dismissible = true,
  closeOnScrim = false,
  panelClassName,
  zClassName = "z-40",
  scrimClassName = "backdrop-blur-sm",
  variant = "default",
  onExited,
}: Props) {
  const { mounted, phase } = usePresence(open, EXIT_MS.modal);
  const id = useId();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const dismissibleRef = useRef(dismissible);
  dismissibleRef.current = dismissible;
  const onExitedRef = useRef(onExited);
  onExitedRef.current = onExited;
  const wasMounted = useRef(mounted);

  useEffect(() => {
    if (wasMounted.current && !mounted) onExitedRef.current?.();
    wasMounted.current = mounted;
  }, [mounted]);

  /* Escape and focus. Registered for `open`, not `mounted`: a panel on its
   * way out no longer owns the keyboard. */
  useEffect(() => {
    if (!open) return;
    modalStack.push(id);
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (modalStack[modalStack.length - 1] !== id) return;
      /* Capture phase on window, stopped here: the canvas and side panel
       * also read Escape (deselect, close panel) and must not react to the
       * keystroke that closed a dialog. */
      event.stopPropagation();
      if (dismissibleRef.current) onCloseRef.current();
    };
    window.addEventListener("keydown", onKeyDown, true);
    /* A dialog's own autofocus (a name field, a search box) runs first in a
     * zero-delay timer; this one only fills in when nothing inside got it. */
    const focusTimer = window.setTimeout(() => {
      const panel = panelRef.current;
      if (!panel || panel.contains(document.activeElement)) return;
      const target = panel.querySelector<HTMLElement>(
        "[autofocus], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled]), [href], [tabindex]:not([tabindex='-1'])",
      );
      (target ?? panel).focus({ preventScroll: true });
    }, 0);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.clearTimeout(focusTimer);
      const index = modalStack.lastIndexOf(id);
      if (index >= 0) modalStack.splice(index, 1);
      /* Hand focus back to whatever opened the dialog, if it is still on the
       * page and nothing else has claimed focus since. */
      const active = document.activeElement;
      const lost = !active || active === document.body || panelRef.current?.contains(active);
      if (lost && opener && opener.isConnected) opener.focus({ preventScroll: true });
    };
  }, [open, id]);

  if (!mounted) return null;

  return createPortal(
    <div
      data-phase={phase}
      className={`mc-scrim fixed inset-0 ${zClassName} flex items-center justify-center bg-surface-scrim/60 ${scrimClassName}`}
      onMouseDown={(event) => {
        if (!closeOnScrim || !dismissible || event.target !== event.currentTarget) return;
        onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={labelledBy ? undefined : label}
        aria-labelledby={labelledBy}
        tabIndex={-1}
        data-phase={phase}
        data-variant={variant}
        className={`mc-modal outline-none ${panelClassName}`}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
