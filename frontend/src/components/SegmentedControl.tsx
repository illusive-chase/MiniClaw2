import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

export type SegmentedOption<T extends string> = {
  value: T;
  label: ReactNode;
  title?: string;
};

type Props<T extends string> = {
  value: T;
  onChange: (value: T) => void;
  options: ReadonlyArray<SegmentedOption<T>>;
  /** Container classes (border, background, padding). */
  className: string;
  /** Shared option classes (padding, type). */
  optionClassName: string;
  activeClassName: string;
  inactiveClassName: string;
  /** The sliding selection block: fill, radius, shadow, and its vertical
   * inset (`inset-y-0.5` to sit inside a `p-0.5` track). */
  thumbClassName: string;
  disabled?: boolean;
  /** Selecting the current option again is a no-op button press. */
  disableActive?: boolean;
};

/**
 * A segmented control whose selection is one block that slides between
 * options, so a switch reads as movement rather than two separate repaints.
 *
 * The thumb is measured from the active button's `offsetLeft`/`offsetWidth`
 * (layout values, unaffected by any ancestor transform) and re-measured when
 * the control resizes, e.g. after a font-size change. Its first placement is
 * not transitioned, so the control never slides in from the left on mount.
 */
export function SegmentedControl<T extends string>({
  value,
  onChange,
  options,
  className,
  optionClassName,
  activeClassName,
  inactiveClassName,
  thumbClassName,
  disabled = false,
  disableActive = false,
}: Props<T>) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const buttonsRef = useRef(new Map<T, HTMLButtonElement>());
  const [thumb, setThumb] = useState<{ left: number; width: number } | null>(null);
  const [ready, setReady] = useState(false);

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const measure = () => {
      const button = buttonsRef.current.get(value);
      if (!button) return;
      setThumb((prev) =>
        prev && prev.left === button.offsetLeft && prev.width === button.offsetWidth
          ? prev
          : { left: button.offsetLeft, width: button.offsetWidth },
      );
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => observer.disconnect();
  }, [value, options.length]);

  /* Enable the transition one frame after the first measurement. */
  useLayoutEffect(() => {
    if (!thumb || ready) return;
    const frame = window.requestAnimationFrame(() => setReady(true));
    return () => window.cancelAnimationFrame(frame);
  }, [thumb, ready]);

  return (
    <div ref={containerRef} className={`relative inline-flex ${className}`}>
      {thumb && (
        <span
          aria-hidden="true"
          data-ready={ready}
          className={`mc-seg-thumb pointer-events-none absolute left-0 ${thumbClassName}`}
          style={{ width: thumb.width, transform: `translateX(${thumb.left}px)` }}
        />
      )}
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            ref={(element) => {
              if (element) buttonsRef.current.set(option.value, element);
              else buttonsRef.current.delete(option.value);
            }}
            type="button"
            title={option.title}
            aria-pressed={active}
            disabled={disabled || (disableActive && active)}
            onClick={() => onChange(option.value)}
            className={
              `relative z-[1] transition-colors duration-quick ease-out disabled:cursor-default ${optionClassName} ` +
              (active ? activeClassName : inactiveClassName) +
              /* Before the thumb has been measured, paint the active fill on
               * the button itself so the first frame is never unselected. */
              (active && !thumb ? ` ${thumbClassName}` : "")
            }
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
