/* Discrete font sizes for a Markdown block.
 *
 * The steps are a fixed ladder rather than a continuous percentage because
 * `.md-prose` derives every size (headings, code, tables) from the container
 * font size with `em`. A continuous scale produces values like 15.3px, and
 * the derived sizes then land on half pixels where heading and body rhythm
 * visibly breaks. Seven rungs cover the real range: cramped in a 380px side
 * panel at one end, projected on a wall at the other.
 *
 * Scope: the embedded surfaces (side panel, zoom overlay) are fixed at their
 * density default and expose no control, because a rung chosen while reading a
 * full document is usually wrong for a 380px panel. Only the standalone reading
 * page lets the reader move off the default, and it persists that choice —
 * see `markdownReader.ts`.
 */

export const FONT_STEPS = [12, 13, 14, 16, 18, 20, 24] as const;

/** Which surface a Markdown block is rendered on; picks the default rung. */
export type MarkdownDensity = "panel" | "overlay" | "page";

const DEFAULT_PX: Record<MarkdownDensity, number> = {
  /* These match the sizes hard-coded at the existing call sites, so adopting
   * the control changes nothing until the reader touches it. */
  panel: 13,
  overlay: 14,
  page: 16,
};

export function defaultFontIndex(density: MarkdownDensity): number {
  const px = DEFAULT_PX[density];
  const index = FONT_STEPS.indexOf(px as (typeof FONT_STEPS)[number]);
  /* A density whose default is not on the ladder would silently render at
   * 12px; fall back to the middle rung instead. */
  return index >= 0 ? index : Math.floor(FONT_STEPS.length / 2);
}

/** Clamp rather than wrap: at the ends the button disables, so wrapping would
 * jump from largest to smallest with no way to tell it happened. */
export function clampFontIndex(index: number): number {
  if (!Number.isFinite(index)) return 0;
  return Math.min(FONT_STEPS.length - 1, Math.max(0, Math.round(index)));
}

export function fontPxAt(index: number): number {
  return FONT_STEPS[clampFontIndex(index)];
}
