import type { ProjectIcon } from "../types";
import { glyphDef, hueTextClass } from "../projectIcons";

const SIZES = {
  88: { box: "h-[88px] w-[88px] rounded-[23px]", glyph: 40, stroke: 1.8 },
  64: { box: "h-16 w-16 rounded-[17px]", glyph: 30, stroke: 1.9 },
  28: { box: "h-7 w-7 rounded-lg", glyph: 15, stroke: 2.1 },
  20: { box: "h-5 w-5 rounded-[6px]", glyph: 12, stroke: 2.3 },
} as const;

export type GlyphSize = keyof typeof SIZES;

/**
 * One project's look: a white card with the glyph stroked in its hue.
 *
 * Every project shares the card; only the stroke is colored, so a grid of them
 * reads as one surface rather than a patchwork of tinted squares. `icon: null`
 * draws the empty slot the new-project form shows before anything is chosen.
 */
export function ProjectGlyph({
  icon,
  size = 64,
  className = "",
}: {
  /** A glyph without a hue yet (mid-choice in the new-project form) strokes in ink. */
  icon: ProjectIcon | { glyph: string; hue: null } | null;
  size?: GlyphSize;
  className?: string;
}) {
  const spec = SIZES[size];
  if (!icon) {
    return (
      <span
        aria-hidden="true"
        className={`inline-grid flex-none place-items-center border-[1.5px] border-dashed border-line-strong ${spec.box} ${className}`}
      />
    );
  }
  const Glyph = glyphDef(icon.glyph).icon;
  return (
    <span aria-hidden="true" className={`home-tile flex-none ${spec.box} ${className}`}>
      <Glyph
        size={spec.glyph}
        strokeWidth={spec.stroke}
        className={icon.hue ? hueTextClass(icon.hue) : "text-ink-muted"}
      />
    </span>
  );
}
