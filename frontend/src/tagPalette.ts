/* Tag color palette (design §1.2).
 *
 * The eight keys and their CSS tokens live in `index.css` / `tailwind.config.ts`;
 * this module is the render side. The backend (`tags.py`) holds its own copy of
 * the same key list for validation — two copies is intentional, so CSS tokens
 * never have to be reachable from Python.
 */

/** Ordered — the palette picker and grouped-sort sections both follow it. */
export const TAG_COLORS = [
  "coral",
  "amber",
  "sage",
  "teal",
  "azure",
  "indigo",
  "plum",
  "clay",
] as const;

export type TagColor = (typeof TAG_COLORS)[number];

/** Reserved for the "未分类" bucket. Never offered as a tag color. */
export const NEUTRAL_TAG_COLOR = "neutral";

type ChipTone = TagColor | typeof NEUTRAL_TAG_COLOR;

/* Tailwind scans source statically, so every class has to appear as a literal.
 * `bg-tag-${color}-soft` would compile to nothing. */
const CHIP_CLASS: Record<ChipTone, string> = {
  coral: "bg-tag-coral-soft text-tag-coral border-tag-coral/25",
  amber: "bg-tag-amber-soft text-tag-amber border-tag-amber/25",
  sage: "bg-tag-sage-soft text-tag-sage border-tag-sage/25",
  teal: "bg-tag-teal-soft text-tag-teal border-tag-teal/25",
  azure: "bg-tag-azure-soft text-tag-azure border-tag-azure/25",
  indigo: "bg-tag-indigo-soft text-tag-indigo border-tag-indigo/25",
  plum: "bg-tag-plum-soft text-tag-plum border-tag-plum/25",
  clay: "bg-tag-clay-soft text-tag-clay border-tag-clay/25",
  neutral: "bg-tag-neutral-soft text-tag-neutral border-tag-neutral/25",
};

const DOT_CLASS: Record<ChipTone, string> = {
  coral: "bg-tag-coral",
  amber: "bg-tag-amber",
  sage: "bg-tag-sage",
  teal: "bg-tag-teal",
  azure: "bg-tag-azure",
  indigo: "bg-tag-indigo",
  plum: "bg-tag-plum",
  clay: "bg-tag-clay",
  neutral: "bg-tag-neutral",
};

const SWATCH_CLASS: Record<TagColor, string> = {
  coral: "bg-tag-coral",
  amber: "bg-tag-amber",
  sage: "bg-tag-sage",
  teal: "bg-tag-teal",
  azure: "bg-tag-azure",
  indigo: "bg-tag-indigo",
  plum: "bg-tag-plum",
  clay: "bg-tag-clay",
};

export function isTagColor(value: string): value is TagColor {
  return (TAG_COLORS as readonly string[]).includes(value);
}

/** Falls back to neutral so a color this build does not know still renders. */
function tone(color: string): ChipTone {
  return isTagColor(color) ? color : NEUTRAL_TAG_COLOR;
}

/** Fill + text + border for a chip. */
export function tagChipClass(color: string): string {
  return CHIP_CLASS[tone(color)];
}

/** The color dot inside a chip, or a section header's dot. */
export function tagDotClass(color: string): string {
  return DOT_CLASS[tone(color)];
}

/** Solid swatch for the color picker. */
export function tagSwatchClass(color: TagColor): string {
  return SWATCH_CLASS[color];
}

/* Default color for a new tag, so a user who types a name and hits enter gets
 * something distinct without picking (design §1.2). The name is the seed rather
 * than a counter so "urgent" lands on the same color every time, which lets a
 * user lean on it semantically.
 *
 * This is a plain FNV-1a, not the backend's sha256: the two never have to agree
 * because the create call always sends an explicit `color`, so what the picker
 * previewed is what gets stored. The backend's own default only applies to
 * clients that omit the field. */
export function defaultColorForName(name: string): TagColor {
  const seed = name.trim().toLowerCase();
  let hash = 0x811c9dc5;
  for (let i = 0; i < seed.length; i += 1) {
    hash ^= seed.charCodeAt(i);
    /* Math.imul keeps the 32-bit wrap; `*` would lose precision past 2^53. */
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return TAG_COLORS[hash % TAG_COLORS.length];
}
