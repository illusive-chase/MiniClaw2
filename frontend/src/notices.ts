/* Banner notices: transient presentation of workspace node transitions.
 *
 * A notice is an *immutable record of an event* — "at T, node N entered state
 * B" — and not a view of that node's state. Once created it never follows the
 * node again: answering a gate does not retract its banner, a cancellation
 * does not reclaim a `done` banner, and three transitions in a row produce
 * three independent banners.
 *
 * That is the load-bearing decision of this module, so the reducer surface is
 * deliberately narrow: `push`, `dismiss`, `expire`. There is intentionally no
 * action that takes a node's *current* state as input. Adding one would
 * reintroduce retraction, reconciliation against the live node list, and a
 * whole class of races that this design does not have. The cost is that a
 * banner can outlive the situation it describes, which is acceptable: it says
 * "this happened", and that stays true. Live state is expressed by the node
 * tiles, the run-status panel, and the side panel.
 */

import { useCallback, useRef, useState } from "react";

import { activeStateLabel, notificationKey, rowContext } from "./activeNodes";
import type { ActiveNodeEntry, NodeState, WorkspaceEvent } from "./types";

/**
 * How much attention a notice asks for.
 *
 * The split that matters is persistent vs. transient. Persistent kinds are
 * "the result of this turn" and are worth one acknowledgement each; transient
 * kinds are scheduler chatter that is interesting for a moment and then noise.
 */
export type NoticeKind = "blocking" | "failure" | "success" | "neutral" | "progress";

const PERSISTENT_KINDS: ReadonlySet<NoticeKind> = new Set<NoticeKind>([
  "blocking",
  "failure",
  "success",
]);

/** Whether a notice waits for the user rather than expiring on a timer. */
export function isPersistentKind(kind: NoticeKind): boolean {
  return PERSISTENT_KINDS.has(kind);
}

/* `virtual` is absent on purpose: a virtual node is a plan entry, not
 * something that happened. */
const KIND_BY_STATE: Partial<Record<NodeState, NoticeKind>> = {
  waiting: "blocking",
  awaiting_human_input: "blocking",
  error: "failure",
  done: "success",
  cancelled: "neutral",
  running: "progress",
  queued: "progress",
};

/** The notice a state transition deserves, or null for states that get none. */
export function noticeKind(state: NodeState): NoticeKind | null {
  return KIND_BY_STATE[state] ?? null;
}

/**
 * Lifetime per kind; null means "until the user acts on it".
 *
 * `progress` is short because those banners exist only to show that something
 * moved — a template stamping eight nodes emits sixteen of them, and anything
 * longer would bury the results underneath.
 */
export const NOTICE_TTL_MS: Record<NoticeKind, number | null> = {
  blocking: null,
  failure: null,
  success: null,
  neutral: 6000,
  progress: 2500,
};

export function noticeTtlMs(kind: NoticeKind): number | null {
  return NOTICE_TTL_MS[kind];
}

/**
 * The banner's headline: what happened, phrased as the event.
 *
 * Keyed on state rather than kind because the two blocking states are the same
 * *class* of interruption but not the same request — a gate wants an answer, a
 * review node wants a reading — and the headline is exactly where that
 * difference belongs. `activeStateLabel` is the caller-facing fallback for a
 * state that gains a notice kind before it gains a title.
 */
const NOTICE_TITLE_BY_STATE: Partial<Record<NodeState, string>> = {
  waiting: "等你应答",
  awaiting_human_input: "等你审阅",
  error: "运行失败",
  done: "运行完成",
  cancelled: "已取消",
  running: "开始运行",
  queued: "进入队列",
};

export function noticeTitle(notice: Notice): string {
  return (
    NOTICE_TITLE_BY_STATE[notice.entry.state] ?? activeStateLabel(notice.entry.state)
  );
}

/**
 * The description line under the headline: what this event is *about*.
 *
 * Preference order is most-specific-first. A gate summary is the actual
 * question being asked, so it outranks everything; the node label is what the
 * turn was for; `rowContext` is the last resort and is mostly a duration, which
 * the meta row already carries — so it is a filler rather than a choice.
 */
export function noticeBody(notice: Notice): string {
  if (notice.entry.gate) return notice.entry.gate.summary;
  if (notice.entry.label) return notice.entry.label;
  return rowContext(notice.entry, notice.createdAt);
}

export type Notice = {
  /** Unique per banner. One node can hold several banners at once. */
  id: string;
  kind: NoticeKind;
  /** The entry as it was at transition time, never refreshed afterwards. */
  entry: ActiveNodeEntry;
  /** Shared with the history panel so both surfaces mark the same thing read. */
  readKey: string;
  createdAt: number;
  ttlMs: number | null;
};

/**
 * Hard ceiling on banners held at once.
 *
 * Overflow drops the oldest rather than aggregating (batches are absorbed by
 * the rail's bounded scroll area instead). Dropping is safe because the
 * history panel keeps the complete record — what is lost is a banner, not an
 * event.
 */
export const NOTICE_LIMIT = 50;

/**
 * Derive a banner from a pushed transition, or null if it deserves none.
 *
 * This is the *only* way a notice comes into being, and it takes one event
 * rather than a list of entries. That asymmetry is what keeps a reconnect
 * quiet: the snapshot path (`useActiveNodes`'s `refresh`) replaces the whole
 * entry array and never reaches here, so a dropped WebSocket does not
 * redeliver a screenful of banners for work the user already saw.
 */
export function noticeFromEvent(
  event: WorkspaceEvent,
  now: number,
  seq: number,
): Notice | null {
  /* A removal is a record disappearing, not something happening. */
  if (event.type !== "workspace_node_updated") return null;
  const entry = event.entry;
  /* No transition, no event. The backend does not emit these, but anything
   * that ever synthesizes an event from a snapshot row would land here, and
   * it must stay silent. */
  if (event.previous_state === entry.state) return null;
  const kind = noticeKind(entry.state);
  if (kind === null) return null;
  return {
    id: `${entry.node_id}:${entry.state}:${seq}`,
    kind,
    entry,
    readKey: notificationKey(entry),
    createdAt: now,
    ttlMs: noticeTtlMs(kind),
  };
}

/**
 * Prepend a notice, evicting past the cap.
 *
 * Newest first, purely by arrival — never reordered by urgency. A banner rail
 * is a time stream, and a row that jumps position after the fact is a row the
 * user cannot follow. Urgency is carried by color and icon instead.
 *
 * Eviction takes transient notices before persistent ones: a `progress` banner
 * was about to vanish on its own, so losing it costs nothing, while a
 * persistent one is a result nobody has acknowledged yet.
 */
export function pushNotice(
  notices: Notice[],
  notice: Notice,
  limit: number = NOTICE_LIMIT,
): Notice[] {
  const next = [notice, ...notices];
  if (next.length <= limit) return next;
  const overflow = next.length - limit;
  const victims = new Set<string>();
  for (let i = next.length - 1; i >= 0 && victims.size < overflow; i -= 1) {
    if (!isPersistentKind(next[i].kind)) victims.add(next[i].id);
  }
  /* Still over the cap: fall back to plain oldest-first, which now means the
   * oldest persistent banners. */
  for (let i = next.length - 1; i >= 0 && victims.size < overflow; i -= 1) {
    victims.add(next[i].id);
  }
  return next.filter((item) => !victims.has(item.id));
}

export function removeNotice(notices: Notice[], id: string): Notice[] {
  return notices.filter((notice) => notice.id !== id);
}

/**
 * Whether an entry's state is one the unread badge counts.
 *
 * Only persistent kinds count. Running and queued nodes are perpetual on a
 * busy workspace, and a badge that is never zero conveys nothing — it is
 * ignored within a day. Those states have their own button now (run status),
 * where a count that rises and falls with the machine is exactly right.
 */
export function countsTowardBadge(entry: ActiveNodeEntry): boolean {
  const kind = noticeKind(entry.state);
  return kind !== null && isPersistentKind(kind);
}

export function badgeCountable(entries: ActiveNodeEntry[]): ActiveNodeEntry[] {
  return entries.filter(countsTowardBadge);
}

/* ───────────── system notifications ───────────── */

export type SystemNotificationPermission =
  | "unsupported"
  | "default"
  | "granted"
  | "denied";

/* Every accessor below is total. The API is missing in insecure contexts and
 * older browsers, and both reading and constructing can throw in embedded
 * webviews; the in-page rail already carries the information, so a failure
 * here must never surface. */

export function systemNotificationPermission(): SystemNotificationPermission {
  try {
    if (typeof window === "undefined" || !("Notification" in window)) {
      return "unsupported";
    }
    const permission = window.Notification.permission;
    return permission === "granted" || permission === "denied" ? permission : "default";
  } catch {
    return "unsupported";
  }
}

/**
 * Ask for permission. Only ever called from a user gesture.
 *
 * Never prompted at startup: an unsolicited permission dialog is the one most
 * likely to be dismissed, and a denial is permanent — the browser will not ask
 * again. So the rail offers a button on the first blocking banner instead, at
 * the moment the value of the permission is visible.
 */
export async function requestSystemNotificationPermission(): Promise<SystemNotificationPermission> {
  if (systemNotificationPermission() === "unsupported") return "unsupported";
  try {
    const result = await window.Notification.requestPermission();
    return result === "granted" || result === "denied" ? result : "default";
  } catch {
    return "unsupported";
  }
}

/**
 * Break out of the browser window for a blocking transition.
 *
 * Blocking is the only class that earns this. An unanswered ask-user gate has
 * no timeout and still occupies a scheduling slot, and projects default to a
 * concurrency of one — so nobody answering does not merely delay one node, it
 * stalls the whole project. Everything else can wait for the user to look.
 *
 * Suppressed while the tab is visible, where the banner rail is strictly
 * better: it is in context and it can be clicked through to the node.
 *
 * `onActivate` receives the whole notice rather than its entry because clicking
 * a system notification is the same acknowledgement as clicking the in-page
 * banner — the caller has to settle *this banner* and its read key, not merely
 * navigate to the node it names.
 */
export function emitSystemNotification(
  notice: Notice,
  onActivate: (notice: Notice) => void,
): void {
  if (notice.kind !== "blocking") return;
  if (systemNotificationPermission() !== "granted") return;
  if (typeof document !== "undefined" && document.visibilityState === "visible") {
    return;
  }
  try {
    const handle = new window.Notification(
      notice.entry.project_name || "未命名项目",
      {
        body: rowContext(notice.entry, notice.createdAt) || "节点在等你应答",
        /* One live notification per node: a node that flips between blocking
         * states should replace its own notice, not stack up. */
        tag: notice.entry.node_id,
      },
    );
    handle.onclick = () => {
      try {
        window.focus();
        handle.close();
      } catch {
        /* focus is best-effort; the activation below is the part that matters */
      }
      onActivate(notice);
    };
  } catch {
    /* Permission can be granted while construction still fails in some
     * embedded contexts. The rail already showed this. */
  }
}

/* ───────────── controller ───────────── */

export type NoticesController = {
  notices: Notice[];
  /** Feed one pushed transition. Safe to call for every workspace event. */
  push: (event: WorkspaceEvent) => void;
  /** User closed or clicked through a banner. */
  dismiss: (id: string) => void;
  /** A transient banner's timer ran out. Leaves it unread. */
  expire: (id: string) => void;
};

/**
 * Own the banner list.
 *
 * `seq` rather than a timestamp gives ids their uniqueness: two transitions
 * inside the same millisecond are ordinary when a template stamps a lane, and
 * colliding ids would make React reuse one banner's DOM for another.
 *
 * `onActivate` is what clicking a *system* notification does. It takes the
 * notice, not the entry, so the caller can route it through the same
 * acknowledgement it gives a banner click: opening a notification the browser
 * raised is at least as deliberate an acknowledgement as clicking the in-page
 * banner, so the banner must go and its read key must settle. Handing over only
 * the entry left the user staring at the banner they had just answered.
 */
export function useNotices(
  onActivate: (notice: Notice) => void,
): NoticesController {
  const [notices, setNotices] = useState<Notice[]>([]);
  const seqRef = useRef(0);
  const onActivateRef = useRef(onActivate);
  onActivateRef.current = onActivate;

  const push = useCallback((event: WorkspaceEvent) => {
    seqRef.current += 1;
    const notice = noticeFromEvent(event, Date.now(), seqRef.current);
    if (!notice) return;
    setNotices((current) => pushNotice(current, notice));
    emitSystemNotification(notice, (item) => onActivateRef.current(item));
  }, []);

  const dismiss = useCallback((id: string) => {
    setNotices((current) => removeNotice(current, id));
  }, []);

  return { notices, push, dismiss, expire: dismiss };
}
