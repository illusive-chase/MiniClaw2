/* What the code-reading page remembers between visits.
 *
 * Per project: the open tabs, which one was active, the expanded folders and
 * the tree width. Globally: the code font size. Like `theme.ts`, every read
 * and write tolerates blocked site data — the page then simply starts fresh.
 */

import type { CodeTab } from "./codeTabs";

export type StoredReader = {
  v: 1;
  tabs: CodeTab[];
  active: string | null;
  expanded: string[];
  treeWidth: number;
};

export const FONT_STEPS = [11, 12, 13, 14, 15, 16, 18, 20] as const;
export const DEFAULT_FONT = 13;
export const TREE_WIDTH_DEFAULT = 280;
export const TREE_WIDTH_MIN = 180;
export const TREE_WIDTH_MAX = 560;

const PREFS_KEY = "miniclaw2:code-reader:prefs";

export function readerKey(sessionId: string): string {
  return `miniclaw2:code-reader:${sessionId}`;
}

export function clampTreeWidth(width: number): number {
  if (!Number.isFinite(width)) return TREE_WIDTH_DEFAULT;
  return Math.min(TREE_WIDTH_MAX, Math.max(TREE_WIDTH_MIN, Math.round(width)));
}

function isTab(value: unknown): value is CodeTab {
  if (!value || typeof value !== "object") return false;
  const tab = value as Partial<CodeTab>;
  return typeof tab.path === "string" && typeof tab.preview === "boolean";
}

export function readStoredReader(sessionId: string): StoredReader | null {
  try {
    const raw = localStorage.getItem(readerKey(sessionId));
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<StoredReader>;
    if (value?.v !== 1 || !Array.isArray(value.tabs)) return null;
    const tabs = value.tabs.filter(isTab).map((tab) => ({
      path: tab.path,
      preview: tab.preview,
      scrollTop: Number(tab.scrollTop) || 0,
      topLine: Math.max(1, Number(tab.topLine) || 1),
    }));
    return {
      v: 1,
      tabs,
      active: typeof value.active === "string" ? value.active : null,
      expanded: Array.isArray(value.expanded) ? value.expanded.filter((e): e is string => typeof e === "string") : [],
      treeWidth: clampTreeWidth(Number(value.treeWidth)),
    };
  } catch {
    return null;
  }
}

export function writeStoredReader(sessionId: string, value: StoredReader): void {
  try {
    localStorage.setItem(readerKey(sessionId), JSON.stringify(value));
  } catch {
    /* Site data blocked or full; the page still works without it. */
  }
}

export function readCodeFont(): number {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    const font = raw ? Number((JSON.parse(raw) as { font?: unknown }).font) : NaN;
    if ((FONT_STEPS as readonly number[]).includes(font)) return font;
  } catch {
    /* fall through */
  }
  return DEFAULT_FONT;
}

export function writeCodeFont(font: number): void {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ font }));
  } catch {
    /* ignore */
  }
}
