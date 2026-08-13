import type { TemplateArgumentDisplay, TemplateInstanceProgress } from "../layout";

/**
 * The shared identity line for a template instance — template name, the
 * arguments it was stamped with, and rolled-up member progress. Used by both
 * the expanded frame's header and the collapsed box so the two views read as
 * the same object.
 */
export function TemplateInstanceSummary({
  label,
  argumentSummary,
  progress,
  stacked = false,
}: {
  label: string;
  argumentSummary: TemplateArgumentDisplay[];
  progress: TemplateInstanceProgress;
  /** Collapsed box lays the parts out vertically; the header band is one row. */
  stacked?: boolean;
}) {
  const args = argumentSummary
    .map((argument) => `${argument.name}=${argument.value}`)
    .join(" · ");
  if (stacked) {
    return (
      <div className="flex min-w-0 flex-col gap-1">
        <span className="truncate font-display text-[12px] leading-tight text-ink">
          {label}
        </span>
        {args && (
          <span className="truncate font-mono text-[9px] leading-tight text-ink-subtle" title={args}>
            {args}
          </span>
        )}
      </div>
    );
  }
  return (
    <>
      <span className="truncate text-[11px] font-medium leading-none">{label}</span>
      {args && (
        <span
          className="truncate font-mono text-[9px] leading-none opacity-70"
          title={args}
        >
          {args}
        </span>
      )}
      <span className="ml-auto flex-none font-mono text-[9px] leading-none opacity-70">
        {progressLabel(progress)}
      </span>
    </>
  );
}

/** `3/4 done`, or `1 error · 3/4 done` when any member failed. */
export function progressLabel(progress: TemplateInstanceProgress): string {
  const counts = `${progress.done}/${progress.total} done`;
  if (progress.hasError) return `error · ${counts}`;
  if (progress.running > 0) return `running · ${counts}`;
  return counts;
}
