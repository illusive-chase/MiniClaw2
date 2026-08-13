/** Persisted shape of the library dock's tree: which sections are open, and
 * which directories inside each are expanded.
 *
 * Flat lists could afford to reset on every mount. A tree cannot — reopening
 * `lark › workflow` by hand after every panel toggle is the cost the tree
 * would otherwise add (design §3.1). */

export const LIBRARY_TREE_STORAGE_KEY = "miniclaw.libraryTree";

export type LibrarySectionKey = "templates" | "principles" | "skills";

export const LIBRARY_SECTION_KEYS: readonly LibrarySectionKey[] = [
  "templates",
  "principles",
  "skills",
];

/** Section open/closed defaults, unchanged from the pre-tree dock. */
export const DEFAULT_OPEN_SECTIONS: Record<LibrarySectionKey, boolean> = {
  templates: true,
  principles: false,
  skills: false,
};

export type LibraryTreeState = {
  open: Record<LibrarySectionKey, boolean>;
  /** Expanded directory path keys per section, as produced by `pathKey`. */
  expanded: Record<LibrarySectionKey, string[]>;
};

export function defaultLibraryTreeState(): LibraryTreeState {
  return {
    open: { ...DEFAULT_OPEN_SECTIONS },
    expanded: { templates: [], principles: [], skills: [] },
  };
}

function normalizePaths(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  for (const entry of value) {
    if (typeof entry === "string" && entry.length > 0) seen.add(entry);
  }
  return [...seen];
}

/** Tolerates anything: partial objects, wrong types, a stored `null`. Missing
 * sections fall back to the defaults rather than to "closed", so a truncated
 * record does not silently change how the dock opens. */
export function normalizeLibraryTreeState(value: unknown): LibraryTreeState {
  const state = defaultLibraryTreeState();
  if (!value || typeof value !== "object" || Array.isArray(value)) return state;
  const record = value as { open?: unknown; expanded?: unknown };
  const open =
    record.open && typeof record.open === "object" && !Array.isArray(record.open)
      ? (record.open as Record<string, unknown>)
      : {};
  const expanded =
    record.expanded
    && typeof record.expanded === "object"
    && !Array.isArray(record.expanded)
      ? (record.expanded as Record<string, unknown>)
      : {};
  for (const key of LIBRARY_SECTION_KEYS) {
    if (typeof open[key] === "boolean") state.open[key] = open[key] as boolean;
    state.expanded[key] = normalizePaths(expanded[key]);
  }
  return state;
}

export function readLibraryTreeState(): LibraryTreeState {
  try {
    const raw = window.localStorage.getItem(LIBRARY_TREE_STORAGE_KEY);
    if (raw) return normalizeLibraryTreeState(JSON.parse(raw) as unknown);
  } catch {
    /* unreadable or unparseable — fall through to defaults */
  }
  return defaultLibraryTreeState();
}

export function writeLibraryTreeState(state: LibraryTreeState): void {
  try {
    window.localStorage.setItem(
      LIBRARY_TREE_STORAGE_KEY,
      JSON.stringify(normalizeLibraryTreeState(state)),
    );
  } catch {
    /* localStorage unavailable; in-memory state stays usable */
  }
}
