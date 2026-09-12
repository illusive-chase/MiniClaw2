/* The banner rail: transient, top-left, newest on top.
 *
 * Each banner is one immutable event record (see `notices.ts`) — it never
 * follows the node after it appears. The rail's whole job is to make an event
 * visible for as long as that class of event deserves, and to get out of the
 * way.
 *
 * The rail is a sibling of `<Canvas>`, not a child, so wheel events over it
 * never reach React Flow's pane or its zoom handler. That is what makes the
 * bounded scroll area below workable: those wheel events were already being
 * swallowed by whatever sat here, and this gives them a use.
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { rowElapsed } from "../activeNodes";
import { stateMeta } from "../canvas/nodes/stateMeta";
import {
  isPersistentKind,
  noticeBody,
  noticeTitle,
  requestSystemNotificationPermission,
  systemNotificationPermission,
  type Notice,
  type SystemNotificationPermission,
} from "../notices";
import type { ActiveNodeEntry } from "../types";

type Props = {
  notices: Notice[];
  onJump: (entry: ActiveNodeEntry) => void;
  /** Dismissal is an acknowledgement, so it settles the unread key too. */
  onDismiss: (notice: Notice) => void;
  /** Timer expiry removes the banner but leaves it unread — it was never seen. */
  onExpire: (notice: Notice) => void;
  /** Clear the whole rail at once. Acknowledges every banner it removes. */
  onClearAll: () => void;
};

/**
 * Below this many banners the rail clears itself one banner at a time.
 *
 * A single notice already carries its own close button directly above it, so a
 * "clear all" spanning the rail would be a second control for the same action,
 * one row further from the pointer.
 */
const CLEAR_ALL_MIN = 2;

/**
 * How long a dismissed banner stays mounted to play its exit.
 *
 * Must match the `notice-banner-leave` animation in `index.css`. The record is
 * already gone from `notices` by then — this is presentation only, so the
 * unread bookkeeping in `App` has settled before the pixels finish moving.
 */
const EXIT_MS = 200;

type RailItem = {
  notice: Notice;
  /** Removed upstream; held here only until its exit animation finishes. */
  leaving: boolean;
};

/**
 * Fold the incoming list into the rendered one, preserving position.
 *
 * A banner that disappeared upstream stays at its own index as `leaving`
 * rather than being dropped, so the ones below it do not jump upward while it
 * is still sliding out. New banners arrive at the head, matching `pushNotice`.
 *
 * Ids carry a push sequence number, so a removed id never comes back and a
 * leaving item can never collide with a live one.
 */
export function mergeRailItems(previous: RailItem[], notices: Notice[]): RailItem[] {
  const live = new Map(notices.map((notice) => [notice.id, notice]));
  const held = previous.map((item) => {
    const fresh = live.get(item.notice.id);
    return fresh ? { notice: fresh, leaving: false } : { notice: item.notice, leaving: true };
  });
  const known = new Set(previous.map((item) => item.notice.id));
  const arrived = notices
    .filter((notice) => !known.has(notice.id))
    .map((notice) => ({ notice, leaving: false }));
  return [...arrived, ...held];
}

/** Derive the banner's time-sensitive context from its immutable event row. */
export function noticeContext(notice: Notice, now: number): string {
  /* Deliberately `rowElapsed` and not `rowContext`: the latter substitutes the
   * gate summary for a blocking row, which this banner already renders in its
   * body. Repeating it here put an unbounded string into a `shrink-0` slot
   * sized for a duration, forcing the banner wider than the rail. */
  return rowElapsed(notice.entry, now);
}

/**
 * Keep dismissed banners mounted long enough to animate away.
 *
 * The alternative — letting the parent hold them — would put presentation
 * timing into the notice reducer, which is deliberately stateless about
 * anything a node is currently doing. So the delay lives here, where it is
 * only ever about pixels.
 */
function useRailItems(notices: Notice[]): RailItem[] {
  const [items, setItems] = useState<RailItem[]>(() =>
    notices.map((notice) => ({ notice, leaving: false })),
  );

  useEffect(() => {
    setItems((current) => mergeRailItems(current, notices));
  }, [notices]);

  /* Reaping is a separate effect from merging on purpose: scheduling a timer
   * inside the updater above would double-fire under StrictMode's dev
   * double-invoke, since an updater must be pure. */
  const timersRef = useRef(new Map<string, number>());
  useEffect(() => {
    const timers = timersRef.current;
    for (const item of items) {
      const id = item.notice.id;
      if (!item.leaving || timers.has(id)) continue;
      timers.set(
        id,
        window.setTimeout(() => {
          timers.delete(id);
          setItems((live) => live.filter((entry) => entry.notice.id !== id));
        }, EXIT_MS),
      );
    }
  }, [items]);

  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      for (const timer of timers.values()) window.clearTimeout(timer);
      timers.clear();
    };
  }, []);

  return items;
}

export function NoticeBannerRail({
  notices,
  onJump,
  onDismiss,
  onExpire,
  onClearAll,
}: Props) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const items = useRailItems(notices);
  const hasItems = items.length > 0;

  /* Persistent result banners can remain visible indefinitely, so their
   * relative timestamp must advance independently of incoming events. One
   * shared clock keeps every banner in sync and stops when the rail is empty. */
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!hasItems) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasItems]);

  /* Prompting for system-notification permission is offered, never forced: an
   * unsolicited dialog is the one most likely to be denied, and a denial is
   * permanent. So the button appears on a blocking banner — the moment the
   * permission's value is on screen — and only while it can still be granted. */
  const [permission, setPermission] = useState<SystemNotificationPermission>(
    () => systemNotificationPermission(),
  );
  const hasBlocking = notices.some((notice) => notice.kind === "blocking");
  const offerPermission = permission === "default" && hasBlocking;

  const requestPermission = useCallback(() => {
    void requestSystemNotificationPermission().then(setPermission);
  }, []);

  /* Keep the user's reading position when a banner arrives above it.
   *
   * New banners insert at the top, so without compensation the list shoves
   * everything down mid-read. Scrolled to the top is the one case where
   * following the new arrival is what the user wants, so only a non-zero
   * scroll position gets anchored.
   *
   * `scrollTop` is captured from the scroll handler rather than from the last
   * commit: the user can scroll long after the last render, which would leave
   * a committed value stale. Height is measured after the DOM updates.
   */
  const scrollTopRef = useRef(0);
  const scrollHeightRef = useRef(0);
  /* Drives the bottom fade. Measured rather than counted: banner heights vary
   * with content, so "how many" cannot tell us whether the rail actually
   * clips. */
  const [overflowing, setOverflowing] = useState(false);

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) {
      /* Rail is empty: reset so the next banner is not measured against a
       * height from before it emptied. */
      scrollHeightRef.current = 0;
      scrollTopRef.current = 0;
      setOverflowing(false);
      return;
    }
    const grew = viewport.scrollHeight - scrollHeightRef.current;
    if (grew > 0 && scrollTopRef.current > 0) {
      viewport.scrollTop = scrollTopRef.current + grew;
      scrollTopRef.current = viewport.scrollTop;
    }
    scrollHeightRef.current = viewport.scrollHeight;
    setOverflowing(viewport.scrollHeight > viewport.clientHeight + 1);
  }, [items]);

  if (items.length === 0) return null;

  return (
    <div
      /* Wider than the banners themselves: the viewport's horizontal padding
       * below is what gives each banner's corner close button somewhere to
       * overhang into. Without the extra width the banners would narrow by
       * that padding instead. */
      className="pointer-events-none absolute left-3 top-3 z-10 w-[376px]"
      role="region"
      aria-label="通知横幅"
      aria-live="polite"
    >
      {/* Clearing the rail is one action attached to the top edge, the way a
          notification centre offers it — not a per-banner chore once several
          have stacked up. Absolute positioning keeps the first banner at the
          exact same y-coordinate when the count crosses `CLEAR_ALL_MIN`; the
          button's centre sits on that banner's top border (`pt-2.5` below).
          Below the threshold the banner's own close button is nearer and says
          the same thing, so this stays out of the way. */}
      {notices.length >= CLEAR_ALL_MIN ? (
        <button
          type="button"
          onClick={onClearAll}
          className="pointer-events-auto absolute left-1/2 top-2.5 z-20 -translate-x-1/2 -translate-y-1/2 rounded-full border border-line bg-surface-raised px-3 py-1 text-[10.5px] font-medium text-ink-muted shadow-card transition hover:border-line-strong hover:text-ink"
        >
          全部清除（{notices.length}）
        </button>
      ) : null}
      <div
        ref={viewportRef}
        onScroll={(event) => {
          scrollTopRef.current = event.currentTarget.scrollTop;
        }}
        /* The padding is structural, not cosmetic: a close button straddling
         * its banner's top-right corner paints outside the banner box, and a
         * scroll container clips at its *padding* box — so the overhang needs
         * padding to live in. `overflow-x-hidden` then guarantees no
         * horizontal scrollbar can appear no matter what a banner contains;
         * `overflow-y-auto` alone computes overflow-x to `auto`, which is how
         * an overlong gate summary used to put one there.
         *
         * `gap-3` rather than `gap-2` for the same reason: at an 8px gap a
         * button overhanging 8px upward would touch the banner above it. */
        className="notice-rail-viewport pointer-events-auto flex max-h-[60vh] flex-col gap-3 overflow-y-auto overflow-x-hidden px-2.5 pb-2 pt-2.5"
      >
        {items.map(({ notice, leaving }) => (
          <NoticeBanner
            key={notice.id}
            notice={notice}
            now={now}
            leaving={leaving}
            offerPermission={offerPermission && notice.kind === "blocking"}
            onRequestPermission={requestPermission}
            onJump={() => {
              onDismiss(notice);
              onJump(notice.entry);
            }}
            onDismiss={() => onDismiss(notice)}
            onExpire={() => onExpire(notice)}
          />
        ))}
      </div>
      {/* Hints that the rail continues below, rather than spending a row on a
          "+N more" counter. Non-interactive so it never eats a click on the
          banner underneath it. */}
      {overflowing ? (
        <div
          aria-hidden="true"
          className="pointer-events-none -mt-2 h-2 bg-gradient-to-b from-transparent to-surface-sunken"
        />
      ) : null}
    </div>
  );
}

function NoticeBanner({
  notice,
  now,
  leaving,
  offerPermission,
  onRequestPermission,
  onJump,
  onDismiss,
  onExpire,
}: {
  notice: Notice;
  now: number;
  leaving: boolean;
  offerPermission: boolean;
  onRequestPermission: () => void;
  onJump: () => void;
  onDismiss: () => void;
  onExpire: () => void;
}) {
  const persistent = isPersistentKind(notice.kind);
  const meta = stateMeta(notice.entry.state);
  /* The event row stays frozen, while its relative timestamp advances against
   * the live clock. This preserves what happened without pinning it at 0s. */
  const context = noticeContext(notice, now);
  const body = noticeBody(notice);

  const onExpireRef = useRef(onExpire);
  onExpireRef.current = onExpire;
  const [hovered, setHovered] = useState(false);

  /* Hover pauses the countdown; leaving restarts it at full length. 2.5s is
   * short enough that a banner could otherwise vanish mid-reach, before the
   * pointer arrives to click it. */
  useEffect(() => {
    if (notice.ttlMs === null || hovered || leaving) return;
    const timer = window.setTimeout(() => onExpireRef.current(), notice.ttlMs);
    return () => window.clearTimeout(timer);
  }, [hovered, leaving, notice.ttlMs]);

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className={
        "group relative shrink-0 rounded-lg border border-line bg-surface-raised shadow-raised " +
        (leaving ? "notice-banner-leave" : "notice-banner-enter")
      }
    >
      <button
        type="button"
        onClick={onJump}
        className="flex w-full items-start gap-2.5 rounded-lg px-3.5 py-3 text-left transition hover:brightness-[0.98]"
        title="跳转到该节点"
      >
        {/* The state badge stands in for a macOS notification's app icon, and
            is now the *only* thing that distinguishes one class of notice from
            another: the frame around it is identical for every kind, so shape
            and color carry the whole signal. Slightly larger than the list
            chip for that reason — at 24px a 8px glyph inside a tinted square
            was reading as a colored dot rather than as a check or a cross. */}
        <span
          className={
            "mt-px flex h-7 w-7 shrink-0 items-center justify-center rounded-md " +
            meta.chipBg +
            " " +
            meta.chipText
          }
          aria-hidden="true"
        >
          <meta.Icon size={14} />
        </span>

        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span className="truncate text-[12.5px] font-semibold leading-5 text-ink-strong">
              {noticeTitle(notice)}
            </span>
            {context ? (
              <span className="ml-auto shrink-0 text-[10px] leading-5 text-ink-subtle">
                {context}
              </span>
            ) : null}
          </span>

          {/* The description: what this event is about. Two lines, because a
              gate summary is a sentence and truncating it to one loses the
              question being asked.
            *
            * No `block` here: Tailwind emits `.block` after `.line-clamp-2`,
            * so the two together resolve to `display:block` and the clamp is
            * silently dropped — a long gate summary rendered every line it
            * had. `break-words` handles the other half: a permission prompt
            * routinely carries an absolute path longer than the banner, which
            * would otherwise be laid out at its full width and only then
            * clipped. */}
          {body ? (
            <span className="mt-0.5 line-clamp-2 break-words text-[11.5px] leading-[1.45] text-ink-muted">
              {body}
            </span>
          ) : null}

          <span className="mt-1.5 flex items-center gap-1.5 text-[10px] leading-4 text-ink-subtle">
            <span className="truncate">
              {notice.entry.project_name || "未命名项目"}
            </span>
            {notice.entry.planspace_title ? (
              <>
                <span className="text-line-strong">·</span>
                <span className="truncate">{notice.entry.planspace_title}</span>
              </>
            ) : null}
            <span className="text-line-strong">·</span>
            <span className="shrink-0 font-mono">
              {notice.entry.node_id.slice(0, 8)}
            </span>
          </span>
        </span>
      </button>

      {/* Straddling the top-right corner, the way iOS and macOS place it: the
        * badge sits half outside the banner so it never covers the text, and
        * reads as an affordance attached to the card rather than a control
        * inside it. It needs a real border and an opaque fill to survive
        * hanging over the corner — half of it paints against the canvas.
        *
        * Revealed on hover so a rail at rest stays quiet. Transient banners
        * leave on their own, so only persistent ones offer it. */}
      {persistent ? (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="关闭此通知"
          title="关闭"
          className="absolute -right-2 -top-2 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-line bg-surface-raised text-ink-muted opacity-0 shadow-card transition hover:border-line-strong hover:text-ink focus-visible:opacity-100 group-hover:opacity-100"
        >
          <svg
            viewBox="0 0 24 24"
            width="13"
            height="13"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      ) : null}

      {offerPermission ? (
        <div className="border-t border-line/60 px-3.5 py-2">
          <button
            type="button"
            onClick={onRequestPermission}
            className="text-[10.5px] text-ink-muted underline decoration-dotted underline-offset-2 transition hover:text-ink"
          >
            开启系统通知，离开页面时也能收到
          </button>
        </div>
      ) : null}
    </div>
  );
}
