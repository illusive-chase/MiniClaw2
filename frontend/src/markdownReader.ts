/* Persisted reading preferences for the standalone Markdown page.
 *
 * These belong to *that* surface only. A 380px side panel and a 1040px overlay
 * have their own sensible sizes, and a preference chosen while reading a full
 * document on a wide display is usually wrong for both — so the controls, and
 * this storage, exist for the reading page alone. See `markdownFont.ts` for why
 * font size is a fixed ladder rather than a continuous scale.
 *
 * Width is the second axis, and the one the ladder is really about: the page
 * caps the column so a document does not run 200+ characters per line on a wide
 * display, but where that cap belongs is a matter of taste and of how far away
 * the reader is sitting. A rung is a `ch` cap on the column, so a narrow rung
 * reads as generous side margins and a wide rung as almost none. The widest rung
 * exceeds most viewport widths, which is what "no margins to speak of" means in
 * practice — there is no separate unbounded mode to reason about.
 *
 * `ch` here resolves against the page's base font size, not the prose font size,
 * so the two controls stay orthogonal: changing the font does not move the
 * column, and the header bar and the body can share one cap and stay aligned.
 *
 * Read and write are total functions that fall back to the defaults rather than
 * throwing, following `projectSort.ts`: localStorage can be unavailable (private
 * windows, disabled site data) or over quota, and the page must still render.
 */

import { clampFontIndex, defaultFontIndex } from "./markdownFont";

export const READER_PREFS_STORAGE_KEY = "miniclaw.mdReader";

/** Column caps in `ch`, narrow to wide. The last rung is past most viewports. */
export const WIDTH_STEPS = [64, 72, 84, 96, 120, 160] as const;

/** 84ch: the cap the page shipped with, and a comfortable measure at 16px. */
export const DEFAULT_WIDTH_CH = 84;

export function defaultWidthIndex(): number {
  const index = WIDTH_STEPS.indexOf(
    DEFAULT_WIDTH_CH as (typeof WIDTH_STEPS)[number],
  );
  /* A default that is not on the ladder would silently render at the narrowest
   * rung; fall back to the middle instead. */
  return index >= 0 ? index : Math.floor(WIDTH_STEPS.length / 2);
}

/** Clamp rather than wrap, for the same reason as the font ladder: at the ends
 *  the button disables, so wrapping would jump widest to narrowest silently. */
export function clampWidthIndex(index: number): number {
  if (!Number.isFinite(index)) return 0;
  return Math.min(WIDTH_STEPS.length - 1, Math.max(0, Math.round(index)));
}

export function widthChAt(index: number): number {
  return WIDTH_STEPS[clampWidthIndex(index)];
}

export type ReaderPrefs = {
  /** Index into `FONT_STEPS`. */
  font: number;
  /** Index into `WIDTH_STEPS`. */
  width: number;
};

export function defaultReaderPrefs(): ReaderPrefs {
  return { font: defaultFontIndex("page"), width: defaultWidthIndex() };
}

/** Coerce anything — a parsed record, a partial, garbage — to a valid pair.
 *  Each axis falls back independently, so one corrupt field does not discard a
 *  preference the reader did set. */
export function normalizeReaderPrefs(value: unknown): ReaderPrefs {
  const fallback = defaultReaderPrefs();
  if (!value || typeof value !== "object") return fallback;
  const raw = value as { font?: unknown; width?: unknown };
  return {
    font:
      typeof raw.font === "number" && Number.isFinite(raw.font)
        ? clampFontIndex(raw.font)
        : fallback.font,
    width:
      typeof raw.width === "number" && Number.isFinite(raw.width)
        ? clampWidthIndex(raw.width)
        : fallback.width,
  };
}

export function readReaderPrefs(): ReaderPrefs {
  try {
    const raw = window.localStorage.getItem(READER_PREFS_STORAGE_KEY);
    if (!raw) return defaultReaderPrefs();
    return normalizeReaderPrefs(JSON.parse(raw));
  } catch {
    /* Unavailable, or a value some other version wrote. Defaults are correct. */
    return defaultReaderPrefs();
  }
}

export function writeReaderPrefs(prefs: ReaderPrefs): void {
  try {
    window.localStorage.setItem(
      READER_PREFS_STORAGE_KEY,
      JSON.stringify(normalizeReaderPrefs(prefs)),
    );
  } catch {
    /* localStorage unavailable; the caller's in-memory state remains usable. */
  }
}
