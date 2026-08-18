/* Cross-project active-node view: polling, ordering, and labels.
 *
 * The WebSocket is per-project, so a node blocked on a human in another
 * project produces no event here. This polls a workspace-wide endpoint
 * instead. Cadence is a few seconds rather than instant on purpose: the
 * signal is "something needs you somewhere", and a human's reaction time
 * dwarfs the delay.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { listActiveNodes } from "./api";
import type { ActiveNodeEntry, NodeCategory, NodeState } from "./types";

export const ACTIVE_NODES_POLL_MS = 2500;

export const WAITING_ONLY_STORAGE_KEY = "miniclaw.activeNodes.waitingOnly";

/* States that mean a human is the blocker, as opposed to the machine being
 * busy. These drive both the "只看等我" filter and the bar's urgency color. */
const HUMAN_BLOCKED_STATES: ReadonlySet<NodeState> = new Set<NodeState>([
  "waiting",
  "awaiting_human_input",
]);

export function isHumanBlocked(entry: ActiveNodeEntry): boolean {
  return HUMAN_BLOCKED_STATES.has(entry.state);
}

export const ACTIVE_STATE_LABELS: Partial<Record<NodeState, string>> = {
  waiting: "等我",
  awaiting_human_input: "等我",
  running: "在跑",
  queued: "排队",
  error: "出错",
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

export type ActiveNodesSummary = {
  waiting: number;
  running: number;
  queued: number;
  error: number;
};

export function summarize(entries: ActiveNodeEntry[]): ActiveNodesSummary {
  const summary: ActiveNodesSummary = { waiting: 0, running: 0, queued: 0, error: 0 };
  for (const entry of entries) {
    if (isHumanBlocked(entry)) summary.waiting += 1;
    else if (entry.state === "error") summary.error += 1;
    else if (entry.state === "running") summary.running += 1;
    else if (entry.state === "queued") summary.queued += 1;
  }
  return summary;
}

/** Which color the always-visible hairline takes; null means stay silent. */
export type BarTone = "waiting" | "running" | "error" | null;

export function barTone(summary: ActiveNodesSummary): BarTone {
  if (summary.waiting > 0) return "waiting";
  if (summary.error > 0) return "error";
  if (summary.running > 0 || summary.queued > 0) return "running";
  return null;
}

/* Same total-function shape as projectSort.ts: localStorage can be
 * unavailable (private windows, disabled storage) and the bar must still
 * render rather than throw on read. */
export function readWaitingOnly(): boolean {
  try {
    return window.localStorage.getItem(WAITING_ONLY_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeWaitingOnly(value: boolean): void {
  try {
    window.localStorage.setItem(WAITING_ONLY_STORAGE_KEY, value ? "1" : "0");
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
 * The trailing hint on a row: what it is blocked on, or how long it has been
 * in its current state.
 *
 * An `error` is terminal, so it reports time since the failure. Measuring
 * from `started_at` against the live clock would render a dead node as having
 * run continuously, with a duration that keeps climbing.
 */
export function rowContext(entry: ActiveNodeEntry, now: number): string {
  if (entry.gate) return `▸ ${entry.gate.summary}`;
  if (entry.state === "error") {
    const since = formatElapsed(entry.finished_at, now);
    return since ? `${since}前失败` : "已失败";
  }
  if (entry.state === "queued") return "等待槽位";
  const elapsed = formatElapsed(entry.started_at, now);
  return elapsed ? `已跑 ${elapsed}` : "";
}

/**
 * Poll the workspace-wide active-node endpoint.
 *
 * Polling pauses while the tab is hidden and re-fetches immediately when it
 * comes back, so a backgrounded window does not keep the backend sweeping.
 * Fetch failures leave the last-known entries in place and are not surfaced:
 * this is ambient status, not the result of a user action, so an error
 * banner here would be noise the user cannot act on.
 */
export function useActiveNodes(enabled: boolean): ActiveNodeEntry[] {
  const [entries, setEntries] = useState<ActiveNodeEntry[]>([]);
  /* Guards against a slow in-flight response landing after the hook is
   * disabled (returning to the landing page) and repopulating the bar. */
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  const refresh = useCallback(async () => {
    if (!enabledRef.current) return;
    try {
      const payload = await listActiveNodes();
      if (enabledRef.current) setEntries(payload.entries);
    } catch {
      /* keep the previous snapshot; the next tick may succeed */
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      setEntries([]);
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

  return entries;
}
