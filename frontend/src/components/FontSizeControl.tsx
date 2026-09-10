/* The [A−] 14 [A+] control for one Markdown block.
 *
 * State belongs to whoever renders this — the overlay, the reading page, a
 * panel — so three surfaces open at once hold three independent sizes and
 * none of them outlive their view. That is the whole scope: no context, no
 * persistence, no keyboard shortcut (Cmd/Ctrl +/- stays the browser's).
 */

import { FONT_STEPS, clampFontIndex } from "../markdownFont";

export type FontSizeControlProps = {
  index: number;
  onChange: (index: number) => void;
  /** Where the double-click / label click resets to. */
  defaultIndex: number;
};

export function FontSizeControl({
  index,
  onChange,
  defaultIndex,
}: FontSizeControlProps) {
  const current = clampFontIndex(index);
  const atMin = current === 0;
  const atMax = current === FONT_STEPS.length - 1;

  return (
    <div className="inline-flex items-center rounded-md border border-line bg-surface-sunken p-0.5">
      <button
        type="button"
        onClick={() => onChange(clampFontIndex(current - 1))}
        disabled={atMin}
        className="flex h-6 w-6 items-center justify-center rounded text-[13px] font-medium text-ink-muted transition hover:text-ink-strong disabled:opacity-35 disabled:hover:text-ink-muted"
        title="减小字号"
        aria-label="减小字号"
      >
        A−
      </button>
      <button
        type="button"
        onClick={() => onChange(defaultIndex)}
        className="min-w-[2.1rem] rounded px-1 text-center font-mono text-[10.5px] text-ink-muted transition hover:text-ink-strong"
        title="重置字号"
        aria-label="重置字号"
      >
        <span aria-live="polite">{FONT_STEPS[current]}</span>
      </button>
      <button
        type="button"
        onClick={() => onChange(clampFontIndex(current + 1))}
        disabled={atMax}
        className="flex h-6 w-6 items-center justify-center rounded text-[13px] font-medium text-ink-muted transition hover:text-ink-strong disabled:opacity-35 disabled:hover:text-ink-muted"
        title="增大字号"
        aria-label="增大字号"
      >
        A+
      </button>
    </div>
  );
}
