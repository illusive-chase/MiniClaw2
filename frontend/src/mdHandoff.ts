/* Handing in-memory Markdown to a new tab.
 *
 * Artifacts and CONTEXT.md have URLs, so the reading page can re-fetch them.
 * Agent output, review handoffs, and preview fields do not — they exist only
 * in the current page's memory. This module parks such text where a new tab
 * can pick it up.
 *
 * `sessionStorage`, not `localStorage`: the handoff is single-use, and a tab
 * that closes should take its parked text with it rather than leaving prose
 * in the user's browser storage indefinitely. A tab opened via `window.open`
 * inherits a snapshot of the opener's sessionStorage, which is exactly the
 * lifetime needed — the new tab reads what existed at the moment it opened.
 *
 * Every entry point degrades to "no handoff" rather than throwing, following
 * draftStash.ts: storage can be unavailable (private windows, disabled site
 * data) or over quota, and that must never take down the view that was only
 * trying to offer a button.
 */

import type { MarkdownLinkBase } from "./types";

const KEY_PREFIX = "miniclaw.mdHandoff:";

/* Bounds on a store the user cannot see or clean up by hand. Ten minutes is
 * generous: the consumer runs milliseconds after `window.open`, and the only
 * reason for any window at all is a tab the browser restores lazily. */
const MAX_ENTRIES = 12;
const MAX_AGE_MS = 10 * 60 * 1000;

export type MarkdownHandoff = {
  title: string;
  subtitle?: string;
  text: string;
  linkBase?: MarkdownLinkBase | null;
  /** Epoch ms, for pruning. */
  savedAt: number;
};

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    /* Access itself throws when site data is blocked. */
    return null;
  }
}

function pruneHandoffs(store: Storage, now: number): void {
  const entries: Array<{ key: string; savedAt: number }> = [];
  for (let i = 0; i < store.length; i += 1) {
    const key = store.key(i);
    if (!key || !key.startsWith(KEY_PREFIX)) continue;
    let savedAt = 0;
    try {
      const parsed: unknown = JSON.parse(store.getItem(key) ?? "null");
      const value =
        parsed && typeof parsed === "object"
        ? (parsed as { savedAt?: unknown }).savedAt
          : undefined;
      savedAt = typeof value === "number" && Number.isFinite(value) ? value : 0;
    } catch {
      savedAt = 0;
    }
    entries.push({ key, savedAt });
  }
  /* An entry with no usable timestamp reads as age zero, which would let it
   * hold a slot forever; drop it outright instead. */
  const stale = entries.filter((e) => e.savedAt === 0 || now - e.savedAt > MAX_AGE_MS);
  const fresh = entries
    .filter((e) => !stale.includes(e))
    .sort((a, b) => b.savedAt - a.savedAt);
  const doomed = [...stale, ...fresh.slice(MAX_ENTRIES - 1)];
  for (const entry of doomed) {
    try {
      store.removeItem(entry.key);
    } catch {
      /* ignore */
    }
  }
}

/** Park `payload` for a new tab. Returns the key, or null if storage failed. */
export function stashMarkdown(payload: {
  title: string;
  subtitle?: string;
  text: string;
  linkBase?: MarkdownLinkBase | null;
}): string | null {
  const store = storage();
  if (!store) return null;
  const now = Date.now();
  pruneHandoffs(store, now);
  const key = `${now.toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  const record: MarkdownHandoff = { ...payload, savedAt: now };
  try {
    store.setItem(`${KEY_PREFIX}${key}`, JSON.stringify(record));
    return key;
  } catch {
    /* Over quota or unavailable. The caller must not open a blank tab. */
    return null;
  }
}

export function readStashedMarkdown(
  key: string,
  now = Date.now(),
): MarkdownHandoff | null {
  const store = storage();
  if (!store) return null;
  let raw: string | null = null;
  try {
    raw = store.getItem(`${KEY_PREFIX}${key}`);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const record = parsed as MarkdownHandoff;
    if (typeof record.text !== "string" || typeof record.title !== "string") {
      return null;
    }
    if (
      typeof record.savedAt !== "number" ||
      !Number.isFinite(record.savedAt) ||
      now - record.savedAt > MAX_AGE_MS
    ) {
      return null;
    }
    return record;
  } catch {
    return null;
  }
}

export const HANDOFF_LIMITS = { MAX_ENTRIES, MAX_AGE_MS, KEY_PREFIX };
