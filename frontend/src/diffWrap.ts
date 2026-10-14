/* Diff viewer line-wrap mode, persisted across reloads.
 *
 * Same shape as `projectSort.ts`: read/write are total functions that fall back
 * to the default rather than throwing, because localStorage can be unavailable
 * (private windows, disabled storage) and the viewer must still render.
 */

export const DIFF_WRAP_STORAGE_KEY = "miniclaw.diffWrap";

export const DIFF_WRAP_MODES = ["wrap", "nowrap"] as const;

export type DiffWrapMode = (typeof DIFF_WRAP_MODES)[number];

/** Wrapping is the default: each side of the split view is only half the
 *  dialog wide, so long lines are the common case rather than the exception. */
export const DEFAULT_DIFF_WRAP: DiffWrapMode = "wrap";

export function normalizeDiffWrap(value: unknown): DiffWrapMode {
  return typeof value === "string"
    && (DIFF_WRAP_MODES as readonly string[]).includes(value)
    ? (value as DiffWrapMode)
    : DEFAULT_DIFF_WRAP;
}

export function readDiffWrap(): DiffWrapMode {
  try {
    return normalizeDiffWrap(window.localStorage.getItem(DIFF_WRAP_STORAGE_KEY));
  } catch {
    return DEFAULT_DIFF_WRAP;
  }
}

export function writeDiffWrap(mode: DiffWrapMode): void {
  try {
    window.localStorage.setItem(DIFF_WRAP_STORAGE_KEY, normalizeDiffWrap(mode));
  } catch {
    /* localStorage unavailable; the caller's in-memory state remains usable */
  }
}
