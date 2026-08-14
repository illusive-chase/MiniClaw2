/* Landing-page sort mode, persisted across reloads (design §1.6).
 *
 * Same shape as `recentUsed.ts`: read/write are total functions that fall back
 * to the default rather than throwing, because localStorage can be unavailable
 * (private windows, disabled storage) and the page must still render.
 */

export const PROJECT_SORT_STORAGE_KEY = "miniclaw.projectSort";

export const PROJECT_SORT_MODES = ["grouped", "name", "activity"] as const;

export type ProjectSortMode = (typeof PROJECT_SORT_MODES)[number];

/** Grouping is the default: it is the only mode that shows what tags exist. */
export const DEFAULT_PROJECT_SORT: ProjectSortMode = "grouped";

export const PROJECT_SORT_LABELS: Record<ProjectSortMode, string> = {
  grouped: "按 tag 分组",
  name: "按名称",
  activity: "按最近活动",
};

export function normalizeProjectSort(value: unknown): ProjectSortMode {
  return typeof value === "string"
    && (PROJECT_SORT_MODES as readonly string[]).includes(value)
    ? (value as ProjectSortMode)
    : DEFAULT_PROJECT_SORT;
}

export function readProjectSort(): ProjectSortMode {
  try {
    return normalizeProjectSort(window.localStorage.getItem(PROJECT_SORT_STORAGE_KEY));
  } catch {
    return DEFAULT_PROJECT_SORT;
  }
}

export function writeProjectSort(mode: ProjectSortMode): void {
  try {
    window.localStorage.setItem(PROJECT_SORT_STORAGE_KEY, normalizeProjectSort(mode));
  } catch {
    /* localStorage unavailable; the caller's in-memory state remains usable */
  }
}
