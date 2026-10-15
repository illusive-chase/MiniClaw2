/* Tabs on the code-reading page, as pure functions.
 *
 * VS Code's default model: a file has at most one tab, at most one tab is a
 * preview (italic, replaced by the next preview open), and opening a file
 * that is already open reuses its tab — moving it to the requested position
 * and overwriting where it was. That overwrite is accepted; browser history
 * (see `CodeReaderPage`) is what keeps the jump *origin* recoverable.
 */

export type CodeTab = {
  path: string;
  preview: boolean;
  scrollTop: number;
  topLine: number;
};

export type TabsState = {
  tabs: CodeTab[];
  active: string | null;
  /** Most recently activated first; closing the active tab falls back here. */
  mru: string[];
};

export type OpenPosition = { scrollTop?: number; topLine?: number };

export const EMPTY_TABS: TabsState = { tabs: [], active: null, mru: [] };

function touch(mru: string[], path: string): string[] {
  return [path, ...mru.filter((entry) => entry !== path)];
}

function placed(tab: CodeTab, position: OpenPosition | undefined): CodeTab {
  if (!position) return tab;
  return {
    ...tab,
    scrollTop: position.scrollTop ?? tab.scrollTop,
    topLine: position.topLine ?? tab.topLine,
  };
}

/** The one entry point: tree clicks, jumps and history all open through it. */
export function openTab(
  state: TabsState,
  path: string,
  options: { preview: boolean; position?: OpenPosition },
): TabsState {
  const existing = state.tabs.find((tab) => tab.path === path);
  if (existing) {
    // Rule 1: reuse whatever is there; preview/pinned state is unchanged.
    return {
      tabs: state.tabs.map((tab) => (tab.path === path ? placed(tab, options.position) : tab)),
      active: path,
      mru: touch(state.mru, path),
    };
  }
  const fresh = placed({ path, preview: options.preview, scrollTop: 0, topLine: 1 }, options.position);
  const previewIndex = state.tabs.findIndex((tab) => tab.preview);
  if (options.preview && previewIndex >= 0) {
    // Rule 2: a new preview takes the old preview's slot.
    const replaced = state.tabs[previewIndex].path;
    const tabs = state.tabs.slice();
    tabs[previewIndex] = fresh;
    return {
      tabs,
      active: path,
      mru: touch(state.mru.filter((entry) => entry !== replaced), path),
    };
  }
  // Rule 3: insert to the right of the active tab.
  const activeIndex = state.tabs.findIndex((tab) => tab.path === state.active);
  const tabs = state.tabs.slice();
  tabs.splice(activeIndex >= 0 ? activeIndex + 1 : tabs.length, 0, fresh);
  return { tabs, active: path, mru: touch(state.mru, path) };
}

export function pinTab(state: TabsState, path: string): TabsState {
  return {
    ...state,
    tabs: state.tabs.map((tab) => (tab.path === path ? { ...tab, preview: false } : tab)),
  };
}

export function activateTab(state: TabsState, path: string): TabsState {
  if (!state.tabs.some((tab) => tab.path === path)) return state;
  return { ...state, active: path, mru: touch(state.mru, path) };
}

export function closeTab(state: TabsState, path: string): TabsState {
  const tabs = state.tabs.filter((tab) => tab.path !== path);
  const mru = state.mru.filter((entry) => entry !== path && tabs.some((tab) => tab.path === entry));
  if (state.active !== path) return { tabs, active: state.active, mru };
  return { tabs, active: mru[0] ?? tabs[tabs.length - 1]?.path ?? null, mru };
}

/** Remember where the reader is in a tab, without changing anything else. */
export function recordPosition(state: TabsState, path: string, position: OpenPosition): TabsState {
  let changed = false;
  const tabs = state.tabs.map((tab) => {
    if (tab.path !== path) return tab;
    const next = placed(tab, position);
    changed = next.scrollTop !== tab.scrollTop || next.topLine !== tab.topLine;
    return next;
  });
  return changed ? { ...state, tabs } : state;
}

/** Drop tabs whose files are gone (restore after the tree is known). */
export function retainTabs(state: TabsState, exists: (path: string) => boolean): TabsState {
  const tabs = state.tabs.filter((tab) => exists(tab.path));
  const mru = state.mru.filter((entry) => tabs.some((tab) => tab.path === entry));
  const active = state.active && tabs.some((tab) => tab.path === state.active)
    ? state.active
    : mru[0] ?? tabs[0]?.path ?? null;
  return { tabs, active, mru };
}

/* ── history entries ──────────────────────────────────────────────────── */

export type NavEntry = {
  kind: "code-nav";
  seq: number;
  path: string;
  scrollTop: number;
  line: number | null;
};

export function isNavEntry(value: unknown): value is NavEntry {
  if (!value || typeof value !== "object") return false;
  const entry = value as Partial<NavEntry>;
  return entry.kind === "code-nav" && typeof entry.seq === "number" && typeof entry.path === "string";
}

/* ── tab titles ───────────────────────────────────────────────────────── */

/** File names, plus the shortest parent suffix that tells duplicates apart. */
export function tabTitles(paths: string[]): Map<string, { name: string; hint: string }> {
  const result = new Map<string, { name: string; hint: string }>();
  const byName = new Map<string, string[]>();
  for (const path of paths) {
    const name = path.split("/").pop() ?? path;
    byName.set(name, [...(byName.get(name) ?? []), path]);
  }
  for (const [name, group] of byName) {
    if (group.length === 1) {
      result.set(group[0], { name, hint: "" });
      continue;
    }
    const parents = group.map((path) => path.split("/").slice(0, -1));
    for (let index = 0; index < group.length; index += 1) {
      const own = parents[index];
      let depth = 1;
      while (depth <= own.length) {
        const suffix = own.slice(-depth).join("/");
        const clash = parents.some((other, j) => j !== index && other.slice(-depth).join("/") === suffix);
        if (!clash) break;
        depth += 1;
      }
      result.set(group[index], { name, hint: own.slice(-depth).join("/") || "/" });
    }
  }
  return result;
}
