export const RECENT_USED_STORAGE_KEY = "miniclaw.recentUsed";
export const RECENT_USED_LIMIT = 8;

export type RecentUsedKind = "skill" | "principle";

export type RecentUsed = Record<RecentUsedKind, string[]>;

function normalizeEntries(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const entries: string[] = [];
  for (const entry of value) {
    if (typeof entry !== "string" || entry.length === 0 || seen.has(entry)) continue;
    seen.add(entry);
    entries.push(entry);
    if (entries.length === RECENT_USED_LIMIT) break;
  }
  return entries;
}

function normalizeRecentUsed(value: unknown): RecentUsed {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { skill: [], principle: [] };
  }
  const record = value as Record<string, unknown>;
  return {
    skill: normalizeEntries(record.skill),
    principle: normalizeEntries(record.principle),
  };
}

export function readRecentUsed(): RecentUsed {
  try {
    const raw = window.localStorage.getItem(RECENT_USED_STORAGE_KEY);
    if (raw) return normalizeRecentUsed(JSON.parse(raw) as unknown);
  } catch {
    /* fall through */
  }
  return { skill: [], principle: [] };
}

export function writeRecentUsed(value: RecentUsed): void {
  try {
    window.localStorage.setItem(
      RECENT_USED_STORAGE_KEY,
      JSON.stringify(normalizeRecentUsed(value)),
    );
  } catch {
    /* localStorage unavailable; the caller's in-memory state remains usable */
  }
}

export function recordRecentUsed(kind: RecentUsedKind, entryId: string): RecentUsed {
  const current = readRecentUsed();
  if (entryId.length === 0) return current;
  const next = {
    ...current,
    [kind]: [entryId, ...current[kind].filter((entry) => entry !== entryId)]
      .slice(0, RECENT_USED_LIMIT),
  };
  writeRecentUsed(next);
  return next;
}
