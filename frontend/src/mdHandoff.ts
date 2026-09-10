/* Handing in-memory Markdown to a new tab.
 *
 * Artifacts and CONTEXT.md have URLs, so the reading page can re-fetch them.
 * Agent output, review handoffs, and preview fields do not — they exist only
 * in the current page's memory. This module parks such text where a new tab
 * can pick it up.
 *
 * Two stores, because one cannot do both jobs:
 *
 * - `localStorage` is the *transfer channel*. It is the only storage both
 *   tabs can see. `sessionStorage` cannot serve here: a new context clones
 *   the opener's session store only when it keeps an opener, and these tabs
 *   are opened with `noopener`, so the reader would find an empty store.
 * - `sessionStorage` in the *reading* tab holds the copy that tab reads from
 *   after the first load. Adopting the record there restores what the
 *   transfer channel cannot give us — text that survives a reload for as long
 *   as the tab is open, and dies with it rather than sitting in the user's
 *   browser storage.
 *
 * So a record lives in `localStorage` only for the moment between the click
 * and the new tab's first read: adoption deletes it. Whatever is never picked
 * up (a tab the user closed before it loaded) is pruned by age and count on
 * the next stash, since nothing else would ever clean it up.
 *
 * Every entry point degrades to "no handoff" rather than throwing, following
 * draftStash.ts: storage can be unavailable (private windows, disabled site
 * data) or over quota, and that must never take down the view that was only
 * trying to offer a button.
 */

import type { MarkdownLinkBase } from "./types";

/** Transfer channel, in `localStorage`. Age-checked and pruned. */
const KEY_PREFIX = "miniclaw.mdHandoff:";
/** The reading tab's own copy, in `sessionStorage`. Lives as long as the tab. */
const TAB_PREFIX = "miniclaw.mdTab:";

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
  /** The session local links in this text resolve against; null if none. */
  sessionId?: string | null;
  /** Epoch ms, for pruning. */
  savedAt: number;
};

function transferStore(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    /* Access itself throws when site data is blocked. */
    return null;
  }
}

function tabStore(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
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
  sessionId?: string | null;
}): string | null {
  const store = transferStore();
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

/** Parse and shape-check one stored record. Age is the caller's business. */
function readRecord(store: Storage | null, key: string): MarkdownHandoff | null {
  if (!store) return null;
  let raw: string | null = null;
  try {
    raw = store.getItem(key);
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
    if (typeof record.savedAt !== "number" || !Number.isFinite(record.savedAt)) {
      return null;
    }
    return record;
  } catch {
    return null;
  }
}

/** Move the record into this tab's own store, so a reload still finds it. */
function adopt(key: string, record: MarkdownHandoff): void {
  const tab = tabStore();
  if (!tab) return;
  try {
    tab.setItem(`${TAB_PREFIX}${key}`, JSON.stringify(record));
  } catch {
    /* Keep the transfer copy: it is the only one that exists. */
    return;
  }
  try {
    transferStore()?.removeItem(`${KEY_PREFIX}${key}`);
  } catch {
    /* Pruning will get it. */
  }
}

export function readStashedMarkdown(
  key: string,
  now = Date.now(),
): MarkdownHandoff | null {
  /* This tab's own copy first, and with no age check: it was adopted by this
   * tab, and it goes away when the tab does. Expiring it would blank a page
   * the user simply left open. */
  const adopted = readRecord(tabStore(), `${TAB_PREFIX}${key}`);
  if (adopted) return adopted;

  const record = readRecord(transferStore(), `${KEY_PREFIX}${key}`);
  if (!record) return null;
  if (now - record.savedAt > MAX_AGE_MS) return null;
  adopt(key, record);
  return record;
}

export const HANDOFF_LIMITS = { MAX_ENTRIES, MAX_AGE_MS, KEY_PREFIX, TAB_PREFIX };
