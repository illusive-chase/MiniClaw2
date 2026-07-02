import { useEffect, useRef } from "react";

export type ContextMenuItem = {
  label: string;
  disabled?: boolean;
  hint?: string;
  onClick?: () => void;
};

type Props = {
  x: number;
  y: number;
  items: ContextMenuItem[];
  onClose: () => void;
};

/**
 * Lightweight positioned context menu. Closes on click-outside, escape,
 * or item selection. Rendered at fixed coordinates on the viewport, so
 * callers pass raw ``event.clientX/clientY``.
 */
export function ContextMenu({ x, y, items, onClose }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const onDown = (event: MouseEvent) => {
      if (!ref.current) return;
      if (!ref.current.contains(event.target as Node)) onClose();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  return (
    <div
      ref={ref}
      className="fixed z-50 min-w-[220px] overflow-hidden rounded-md border border-line bg-surface-raised text-xs shadow-modal"
      style={{ left: x, top: y }}
    >
      {items.map((item, idx) => (
        <button
          key={idx}
          type="button"
          disabled={item.disabled}
          onClick={() => {
            if (item.disabled) return;
            item.onClick?.();
            onClose();
          }}
          className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-ink transition hover:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-40"
        >
          <span className="truncate">{item.label}</span>
          {item.hint && (
            <span className="text-[10px] uppercase tracking-[0.14em] text-ink-subtle">
              {item.hint}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
