/* Cross-project notification feed: polling, unread bookkeeping, ordering.
 *
 * The WebSocket is per-project, so a node blocked on a human — or one that
 * simply finished — in another project produces no event here. This polls a
 * workspace-wide endpoint instead. Cadence is measured in seconds rather than
 * instant on purpose: the signal is "something happened somewhere", and a
 * human's reaction time dwarfs the delay.
 *
 * Unread is tracked per device, keyed on the *event* rather than a clock. See
 * `notificationKey` for why a timestamp watermark cannot work here.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { listActiveNodes } from "./api";
import type { ActiveNodeEntry, NodeCategory, NodeState } from "./types";

export const ACTIVE_NODES_POLL_MS = 15_000;

/* A fresh key: the old `activeNodes.waitingOnly` value means a different
 * filter. Reusing it would hand long-time users a filter they never set. */
export const UNREAD_ONLY_STORAGE_KEY = "miniclaw.notifications.unreadOnly";
export const READ_KEYS_STORAGE_KEY = "miniclaw.notifications.readKeys";

/* Bounds the read set so it cannot grow with the store forever. Pruning
 * always keeps keys still present in the feed (see `pruneReadKeys`): evicting
 * one of those would make an already-seen row pop back up as unread. */
export const READ_KEYS_LIMIT = 500;

/* States that mean a human is the blocker, as opposed to the machine being
 * busy or the work being over. */
const HUMAN_BLOCKED_STATES: ReadonlySet<NodeState> = new Set<NodeState>([
  "waiting",
  "awaiting_human_input",
]);

const TERMINAL_STATES: ReadonlySet<NodeState> = new Set<NodeState>([
  "done",
  "error",
  "cancelled",
]);

export function isHumanBlocked(entry: ActiveNodeEntry): boolean {
  return HUMAN_BLOCKED_STATES.has(entry.state);
}

export function isTerminal(entry: ActiveNodeEntry): boolean {
  return TERMINAL_STATES.has(entry.state);
}

export const ACTIVE_STATE_LABELS: Partial<Record<NodeState, string>> = {
  waiting: "等我",
  awaiting_human_input: "等我",
  running: "在跑",
  queued: "排队",
  error: "出错",
  done: "完成",
  cancelled: "取消",
};

export const CATEGORY_LABELS: Record<NodeCategory, string> = {
  planning: "计划",
  regular: "常规",
  review: "审阅",
};

export function activeStateLabel(state: NodeState): string {
  return ACTIVE_STATE_LABELS[state] ?? state;
}

/* Urgency ordering: a human blocked on something outranks a failure, which
 * outranks work that is merely in progress. */
const STATE_RANK: Record<string, number> = {
  waiting: 0,
  awaiting_human_input: 0,
  error: 1,
  running: 2,
  queued: 3,
};

function rankOf(entry: ActiveNodeEntry): number {
  return STATE_RANK[entry.state] ?? 4;
}

/**
 * Sort by urgency, then oldest-first within a group.
 *
 * Oldest-first is deliberate and the opposite of the landing page's
 * most-recent-first: the thing that has been blocked longest is the thing
 * most worth doing next.
 */
export function sortActiveEntries(entries: ActiveNodeEntry[]): ActiveNodeEntry[] {
  return [...entries].sort((a, b) => {
    const byRank = rankOf(a) - rankOf(b);
    if (byRank !== 0) return byRank;
    const at = a.started_at ?? Number.MAX_SAFE_INTEGER;
    const bt = b.started_at ?? Number.MAX_SAFE_INTEGER;
    if (at !== bt) return at - bt;
    return a.node_id.localeCompare(b.node_id);
  });
}

/**
 * Order the feed: live work above, finished work below.
 *
 * The two segments sort in *opposite* directions, and that is not an
 * oversight to be tidied up later. Live nodes are a to-do list, so the
 * longest-blocked comes first (`sortActiveEntries`). Finished nodes are a
 * history of what just happened, so the newest comes first — that is what
 * "what did I miss" means. Unifying these would break one of the two.
 */
export function sortFeed(entries: ActiveNodeEntry[]): ActiveNodeEntry[] {
  const live = entries.filter((entry) => !isTerminal(entry));
  const terminal = entries.filter(isTerminal);
  terminal.sort((a, b) => {
    const at = a.finished_at ?? 0;
    const bt = b.finished_at ?? 0;
    if (at !== bt) return bt - at;
    return a.node_id.localeCompare(b.node_id);
  });
  return [...sortActiveEntries(live), ...terminal];
}

export type ActiveNodesSummary = {
  waiting: number;
  running: number;
  queued: number;
  error: number;
  done: number;
  cancelled: number;
};

export function summarize(entries: ActiveNodeEntry[]): ActiveNodesSummary {
  const summary: ActiveNodesSummary = {
    waiting: 0,
    running: 0,
    queued: 0,
    error: 0,
    done: 0,
    cancelled: 0,
  };
  for (const entry of entries) {
    if (isHumanBlocked(entry)) summary.waiting += 1;
    else if (entry.state === "error") summary.error += 1;
    else if (entry.state === "running") summary.running += 1;
    else if (entry.state === "queued") summary.queued += 1;
    else if (entry.state === "done") summary.done += 1;
    else if (entry.state === "cancelled") summary.cancelled += 1;
  }
  return summary;
}

/**
 * One notification is "this node entered this state", keyed `<node_id>:<state>`.
 *
 * Identity, not time, is the right key. A timestamp watermark ("newer than my
 * last visit is unread") looks simpler but misreads a backend restart: the
 * startup sweep flips every leftover running/waiting node to `cancelled` and
 * stamps them all with the sweep's own `finished_at`, so a pile of old work
 * arrives bearing a brand-new timestamp. A key set compares what a thing *is*
 * and is immune to that.
 *
 * The cost of this choice: a rerun of the same node to the same state reuses
 * the key, so it does not re-notify. Adding `rev` would fix that at the price
 * of a read set that grows with every rerun.
 */
export function notificationKey(entry: ActiveNodeEntry): string {
  return `${entry.node_id}:${entry.state}`;
}

export function isUnread(entry: ActiveNodeEntry, readKeys: ReadonlySet<string>): boolean {
  return !readKeys.has(notificationKey(entry));
}

export function unreadEntries(
  entries: ActiveNodeEntry[],
  readKeys: ReadonlySet<string>,
): ActiveNodeEntry[] {
  return entries.filter((entry) => isUnread(entry, readKeys));
}

type VerticalBounds = {
  top: number;
  bottom: number;
};

/** Whether any vertical portion of a row is inside the scrolling viewport. */
export function isVerticallyVisible(
  row: VerticalBounds,
  viewport: VerticalBounds,
): boolean {
  return row.bottom > viewport.top && row.top < viewport.bottom;
}

/** Badge color. Null means render no badge at all, not a badge showing zero. */
export type BadgeTone = "waiting" | "error" | "done" | null;

/**
 * Which tone the unread count takes.
 *
 * Three tiers, because the badge's whole value is telling the user what kind
 * of attention is owed *without* opening the panel. If a finished node used
 * the same color as one blocked awaiting permission, they would have to open
 * it to find out which — so `done`/`cancelled` get the neutral grey
 * `state-done`, which reads correctly as "over, nothing to do".
 */
export function badgeTone(unread: ActiveNodeEntry[]): BadgeTone {
  if (unread.length === 0) return null;
  if (unread.some(isHumanBlocked)) return "waiting";
  if (unread.some((entry) => entry.state === "error")) return "error";
  return "done";
}

/** Keeps the badge one glyph wide however much piles up. */
export function badgeCountLabel(count: number): string {
  return count >= 10 ? "9+" : String(count);
}

/* localStorage can be unavailable (private windows, disabled storage) or throw
 * on write (quota). Every accessor here is a total function: the bell must
 * render, and degrade to in-memory state, rather than throw. */

export function readReadKeys(): Set<string> | null {
  try {
    const raw = window.localStorage.getItem(READ_KEYS_STORAGE_KEY);
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((item): item is string => typeof item === "string"));
  } catch {
    /* Absent and corrupt are different: null asks for seeding (below), which
     * is right for a first run but wrong for unreadable JSON — that would
     * mark whatever is in the feed as read behind the user's back. */
    return new Set();
  }
}

export function writeReadKeys(keys: ReadonlySet<string>): void {
  try {
    window.localStorage.setItem(READ_KEYS_STORAGE_KEY, JSON.stringify([...keys]));
  } catch {
    /* in-memory state remains usable */
  }
}

/**
 * Drop the oldest keys once past the cap, protecting anything still visible.
 *
 * Keys in the current feed are retained unconditionally: they are exactly the
 * ones whose eviction the user would see, as a row they already read turning
 * unread again. Only keys that have already scrolled out of the window are
 * candidates, and losing those is invisible.
 */
export function pruneReadKeys(
  keys: ReadonlySet<string>,
  feedKeys: ReadonlySet<string>,
  limit: number = READ_KEYS_LIMIT,
): Set<string> {
  if (keys.size <= limit) return new Set(keys);
  const kept: string[] = [];
  const evictable: string[] = [];
  for (const key of keys) {
    (feedKeys.has(key) ? kept : evictable).push(key);
  }
  /* Insertion order approximates age; the tail is the most recently marked. */
  const room = Math.max(0, limit - kept.length);
  return new Set([...kept, ...evictable.slice(evictable.length - room)]);
}

/**
 * Resolve the read set on startup, seeding a first run to zero unread.
 *
 * Without seeding the badge would open on a three-digit number: a real store
 * holds hundreds of long-finished nodes, and every one of them would count as
 * something the user never looked at. A feature whose first frame is "363
 * unread" has already taught the user to ignore it.
 *
 * Only a *missing* read set seeds. An existing-but-empty one means the user
 * cleared it, and re-seeding then would silently discard the very state they
 * asked for.
 */
export function resolveReadKeys(entries: ActiveNodeEntry[]): Set<string> {
  const stored = readReadKeys();
  if (stored !== null) return stored;
  const seeded = new Set(entries.map(notificationKey));
  writeReadKeys(seeded);
  return seeded;
}

export function readUnreadOnly(): boolean {
  try {
    return window.localStorage.getItem(UNREAD_ONLY_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeUnreadOnly(value: boolean): void {
  try {
    window.localStorage.setItem(UNREAD_ONLY_STORAGE_KEY, value ? "1" : "0");
  } catch {
    /* in-memory state remains usable */
  }
}

export function formatElapsed(startedAt: number | null | undefined, now: number): string {
  if (!startedAt) return "";
  const seconds = Math.max(0, Math.floor(now / 1000 - startedAt));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m${String(seconds % 60).padStart(2, "0")}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h${String(minutes % 60).padStart(2, "0")}m`;
}

/**
 * The trailing hint on a row: what it is blocked on, or how long ago it ended.
 *
 * Terminal rows report time since they *finished*. Measuring a dead node from
 * `started_at` against the live clock would render it as having run
 * continuously, with a duration that keeps climbing forever.
 */
export function rowContext(entry: ActiveNodeEntry, now: number): string {
  if (entry.gate) return `▸ ${entry.gate.summary}`;
  if (isTerminal(entry)) {
    const verb =
      entry.state === "error" ? "失败" : entry.state === "cancelled" ? "取消" : "完成";
    const since = formatElapsed(entry.finished_at, now);
    return since ? `${since}前${verb}` : `已${verb}`;
  }
  if (entry.state === "queued") return "等待槽位";
  const elapsed = formatElapsed(entry.started_at, now);
  return elapsed ? `已跑 ${elapsed}` : "";
}

export type ActiveNodesFeed = {
  entries: ActiveNodeEntry[];
  /** True once a fetch has completed, even if it returned nothing.
   *
   * Unread seeding needs this distinction. "No response yet" and "the
   * workspace is quiet" both look like an empty array, but seeding off the
   * latter is correct while seeding off the former would adopt whatever
   * arrives next — the user's first real notification — as already read.
   */
  loaded: boolean;
};

/**
 * Poll the workspace-wide notification endpoint.
 *
 * Polling pauses while the tab is hidden and re-fetches immediately when it
 * comes back, so a backgrounded window does not keep the backend sweeping.
 * Fetch failures leave the last-known entries in place and are not surfaced:
 * this is ambient status, not the result of a user action, so an error
 * banner here would be noise the user cannot act on.
 */
export function useActiveNodes(enabled: boolean): ActiveNodesFeed {
  const [feed, setFeed] = useState<ActiveNodesFeed>({ entries: [], loaded: false });
  /* Guards against a slow in-flight response landing after the hook is
   * disabled (returning to the landing page) and repopulating the feed. */
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  const refresh = useCallback(async () => {
    if (!enabledRef.current) return;
    try {
      const payload = await listActiveNodes();
      if (enabledRef.current) setFeed({ entries: payload.entries, loaded: true });
    } catch {
      /* keep the previous snapshot; the next tick may succeed */
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      setFeed({ entries: [], loaded: false });
      return;
    }
    void refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, ACTIVE_NODES_POLL_MS);
    const onVisibility = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    /* visibilitychange only fires on document; window never receives it. */
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onVisibility);
    };
  }, [enabled, refresh]);

  return feed;
}
