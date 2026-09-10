/* The shared chrome for a discrete stepper: [−] value [+].
 *
 * Two controls on the reading page have this exact shape — font size and column
 * width — and they sit next to each other in the same header, so any drift in
 * padding, height, or disabled styling between them is immediately visible.
 * Keeping the chrome here makes them match structurally rather than by
 * remembering to edit two files.
 *
 * The ladder, the labels, and the reset target belong to the caller; this
 * component owns only the appearance and the disabled/aria wiring.
 */

export type StepperProps = {
  /** Rendered between the buttons, e.g. "16" or "84ch". */
  label: string;
  /** Glyph for the decrement button, e.g. "A−". */
  decGlyph: string;
  /** Glyph for the increment button, e.g. "A+". */
  incGlyph: string;
  onDec: () => void;
  onInc: () => void;
  /** Clicking the label resets; the ends disable rather than wrap. */
  onReset: () => void;
  atMin: boolean;
  atMax: boolean;
  /** Tooltip / screen-reader text, one per button. */
  decTitle: string;
  incTitle: string;
  resetTitle: string;
  /** Keeps the label from reflowing as the value changes width. */
  labelMinWidth?: string;
};

export function Stepper({
  label,
  decGlyph,
  incGlyph,
  onDec,
  onInc,
  onReset,
  atMin,
  atMax,
  decTitle,
  incTitle,
  resetTitle,
  labelMinWidth = "2.1rem",
}: StepperProps) {
  return (
    <div className="inline-flex items-center rounded-md border border-line bg-surface-sunken p-0.5">
      <button
        type="button"
        onClick={onDec}
        disabled={atMin}
        className="flex h-6 w-6 items-center justify-center rounded text-[13px] font-medium text-ink-muted transition hover:text-ink-strong disabled:opacity-35 disabled:hover:text-ink-muted"
        title={decTitle}
        aria-label={decTitle}
      >
        {decGlyph}
      </button>
      <button
        type="button"
        onClick={onReset}
        style={{ minWidth: labelMinWidth }}
        className="rounded px-1 text-center font-mono text-[10.5px] text-ink-muted transition hover:text-ink-strong"
        title={resetTitle}
        aria-label={resetTitle}
      >
        <span aria-live="polite">{label}</span>
      </button>
      <button
        type="button"
        onClick={onInc}
        disabled={atMax}
        className="flex h-6 w-6 items-center justify-center rounded text-[13px] font-medium text-ink-muted transition hover:text-ink-strong disabled:opacity-35 disabled:hover:text-ink-muted"
        title={incTitle}
        aria-label={incTitle}
      >
        {incGlyph}
      </button>
    </div>
  );
}
