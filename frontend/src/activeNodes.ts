/* Cross-project notification feed: pushed updates, unread bookkeeping, ordering.
 *
 * Unread is tracked per device, keyed on the *event* rather than a clock. See
 * `notificationKey` for why a timestamp watermark cannot work here.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { listActiveNodes } from "./api";
import type {
  ActiveNodeEntry,
  NodeCategory,
  NodeState,
  WorkspaceEvent,
} from "./types";
import { useWorkspaceSocket } from "./ws";

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

/**
 * How far back terminal rows stay in the feed. Mirrors the backend's
 * `TERMINAL_RECENCY_SECONDS` (`active_nodes.py`) and must change with it.
 *
 * The server remains the source of truth: it applies this window when building
 * a snapshot. The client re-applies it at render time because a snapshot is
 * only re-fetched on reconnect or tab focus — a tab held in the foreground for
 * longer than the window would otherwise keep displaying rows the server has
 * already stopped listing.
 */
export const TERMINAL_RECENCY_SECONDS = 8 * 3600;

/**
 * Whether a terminal row is still inside the recency window.
 *
 * Live rows are always kept: they are current regardless of age, and a
 * long-running node is the case the feed exists to surface. A terminal row
 * with no `finished_at` is also kept rather than hidden — there is nothing to
 * measure it against, and dropping it would silently lose work that ended.
 */
export function isWithinTerminalWindow(entry: ActiveNodeEntry, now: number): boolean {
  if (!isTerminal(entry)) return true;
  if (!entry.finished_at) return true;
  return now / 1000 - entry.finished_at <= TERMINAL_RECENCY_SECONDS;
}

/** Drop terminal rows the server would no longer include in a snapshot. */
export function withinTerminalWindow(
  entries: ActiveNodeEntry[],
  now: number,
): ActiveNodeEntry[] {
  return entries.filter((entry) => isWithinTerminalWindow(entry, now));
}

/* What the run-status button counts: the machine is busy, or it is stuck on a
 * person. `waiting` deliberately appears here *and* in the notification badge
 * — it is both "this project has not finished" and "this needs me", and the
 * two buttons answer different questions. */
const RUN_STATUS_STATES: ReadonlySet<NodeState> = new Set<NodeState>([
  "running",
  "queued",
  "waiting",
  "awaiting_human_input",
]);

export function isRunStatusEntry(entry: ActiveNodeEntry): boolean {
  return RUN_STATUS_STATES.has(entry.state);
}

/** Rows the run-status panel lists: everything currently in flight. */
export function runStatusEntries(entries: ActiveNodeEntry[]): ActiveNodeEntry[] {
  return entries.filter(isRunStatusEntry);
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
 * Spell a summary out as parts, one per non-zero state.
 *
 * Ordered by urgency, the same ranking `sortActiveEntries` uses, so a
 * breakdown reads in the order the rows below it appear.
 *
 * Every state is covered, including the three the unread badge does not count.
 * A caller summarizing a *filtered* set simply gets no part for the states it
 * filtered out — but a caller summarizing the whole feed must not silently
 * describe only three of the six, or a header ends up claiming nothing is
 * unread while unread rows sit underneath it.
 */
export function summaryParts(summary: ActiveNodesSummary): string[] {
  return [
    summary.waiting > 0 ? `${summary.waiting} 等我` : "",
    summary.error > 0 ? `${summary.error} 出错` : "",
    summary.running > 0 ? `${summary.running} 在跑` : "",
    summary.queued > 0 ? `${summary.queued} 排队` : "",
    summary.done > 0 ? `${summary.done} 已完成` : "",
    summary.cancelled > 0 ? `${summary.cancelled} 取消` : "",
  ].filter(Boolean);
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

/**
 * Whether this activity should ask for the user's attention.
 *
 * Commit ops are bookkeeping nodes. A manual commit was just initiated by the
 * person looking at the Git panel, while an automatic commit immediately
 * follows the agent result that already notified them. Both remain visible in
 * the activity feed and run-status view, but neither should create another
 * unread item or banner for the same work.
 */
export function isNotificationEligible(entry: ActiveNodeEntry): boolean {
  return !(entry.kind === "op" && entry.op_kind === "commit");
}

export function isUnread(entry: ActiveNodeEntry, readKeys: ReadonlySet<string>): boolean {
  return (
    isNotificationEligible(entry) && !readKeys.has(notificationKey(entry))
  );
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

export type ReadKeysController = {
  readKeys: ReadonlySet<string>;
  /** Mark specific notification keys read, persisting the result. */
  markRead: (keys: Iterable<string>) => void;
  /** Mark everything currently in the feed read. */
  markAllRead: () => void;
};

/**
 * Own the read set for every surface that displays notifications.
 *
 * Lifted out of the bell because the banner rail marks the same things read:
 * dismissing a banner and scrolling past its history row are the same
 * acknowledgement, and two components each keeping their own set would let one
 * surface show as unread what the other already settled. One owner, one
 * `localStorage` key.
 */
export function useReadKeys(feed: ActiveNodesFeed): ReadKeysController {
  const { entries, loaded } = feed;
  const [readKeys, setReadKeys] = useState<Set<string>>(() => new Set());

  /* Seeding waits for the first *completed* fetch, not the first non-empty
   * one. On a quiet workspace those differ: seeding only when rows appear
   * would defer the seed to the user's first real notification and mark that
   * one read. `loaded` distinguishes "nothing yet" from "nothing there". */
  const seededRef = useRef(false);
  useEffect(() => {
    if (seededRef.current || !loaded) return;
    seededRef.current = true;
    setReadKeys(resolveReadKeys(entries));
  }, [entries, loaded]);

  /* Pruning protects keys still in the feed, so it needs the live entries
   * without making every caller pass them in. */
  const entriesRef = useRef<ActiveNodeEntry[]>(entries);
  entriesRef.current = entries;

  const markRead = useCallback((keys: Iterable<string>) => {
    setReadKeys((current) => {
      const next = new Set(current);
      let changed = false;
      for (const key of keys) {
        if (next.has(key)) continue;
        next.add(key);
        changed = true;
      }
      if (!changed) return current;
      const pruned = pruneReadKeys(
        next,
        new Set(entriesRef.current.map(notificationKey)),
      );
      writeReadKeys(pruned);
      return pruned;
    });
  }, []);

  const markAllRead = useCallback(() => {
    markRead(entriesRef.current.map(notificationKey));
  }, [markRead]);

  return { readKeys, markRead, markAllRead };
}

export function applyWorkspaceEvent(
  entries: ActiveNodeEntry[],
  event: WorkspaceEvent,
): ActiveNodeEntry[] {
  if (event.type === "workspace_node_removed") {
    return entries.filter((entry) => entry.node_id !== event.node_id);
  }
  const index = entries.findIndex((entry) => entry.node_id === event.node_id);
  if (index < 0) return [...entries, event.entry];
  const next = [...entries];
  next[index] = event.entry;
  return next;
}

/**
 * Seed from the workspace snapshot, then apply pushed node transitions.
 *
 * The snapshot re-fetches when a connection opens or the tab returns to the
 * foreground. Fetch failures leave the last-known entries in place and are not surfaced:
 * this is ambient status, not the result of a user action, so an error
 * banner here would be noise the user cannot act on.
 */
export function useActiveNodes(
  enabled: boolean,
  onWorkspaceEvent?: (event: WorkspaceEvent) => void,
): ActiveNodesFeed {
  const [feed, setFeed] = useState<ActiveNodesFeed>({ entries: [], loaded: false });
  /* Guards against a slow in-flight response landing after the hook is
   * disabled (returning to the landing page) and repopulating the feed. */
  const enabledRef = useRef(enabled);
  const eventVersionRef = useRef(0);
  const onWorkspaceEventRef = useRef(onWorkspaceEvent);
  enabledRef.current = enabled;
  onWorkspaceEventRef.current = onWorkspaceEvent;

  const refresh = useCallback(async () => {
    if (!enabledRef.current) return;
    const version = eventVersionRef.current;
    try {
      const payload = await listActiveNodes();
      if (!enabledRef.current) return;
      if (version !== eventVersionRef.current) {
        void refresh();
        return;
      }
      setFeed({ entries: payload.entries, loaded: true });
    } catch {
      /* keep the previous snapshot; reconnect or focus will retry */
    }
  }, []);

  const handleWorkspaceEvent = useCallback((event: WorkspaceEvent) => {
    eventVersionRef.current += 1;
    setFeed((current) => {
      return { ...current, entries: applyWorkspaceEvent(current.entries, event) };
    });
    onWorkspaceEventRef.current?.(event);
  }, []);

  useWorkspaceSocket(enabled, handleWorkspaceEvent, refresh);

  useEffect(() => {
    if (!enabled) {
      setFeed({ entries: [], loaded: false });
      return;
    }
    const onVisibility = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    /* visibilitychange only fires on document; window never receives it. */
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onVisibility);
    };
  }, [enabled, refresh]);

  return feed;
}
