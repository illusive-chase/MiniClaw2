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

import { rowContext } from "../activeNodes";
import { stateMeta } from "../canvas/nodes/stateMeta";
import {
  isPersistentKind,
  noticeBody,
  noticeTitle,
  requestSystemNotificationPermission,
  systemNotificationPermission,
  type Notice,
  type NoticeKind,
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
};

/* Tone per kind. `stateMeta` owns node-state color, but a banner is keyed on
 * the *class* of event rather than the state, and its border/background pair
 * has no counterpart there — five entries against eight states, and both
 * blocking states share one look. The tokens are the same family. */
const KIND_FRAME: Record<NoticeKind, string> = {
  blocking: "border-state-waiting/45 bg-state-waiting-soft",
  failure: "border-state-error/40 bg-state-error-soft",
  success: "border-state-done/40 bg-surface-raised",
  neutral: "border-line bg-surface-raised",
  progress: "border-line bg-surface-raised",
};

const KIND_TITLE: Record<NoticeKind, string> = {
  blocking: "text-state-waiting",
  failure: "text-state-error",
  success: "text-ink-strong",
  neutral: "text-ink",
  progress: "text-ink",
};

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

export function NoticeBannerRail({ notices, onJump, onDismiss, onExpire }: Props) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const items = useRailItems(notices);

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
      className="pointer-events-none absolute left-3 top-3 z-10 w-[356px]"
      role="region"
      aria-label="通知横幅"
      aria-live="polite"
    >
      <div
        ref={viewportRef}
        onScroll={(event) => {
          scrollTopRef.current = event.currentTarget.scrollTop;
        }}
        className="notice-rail-viewport pointer-events-auto flex max-h-[60vh] flex-col gap-2 overflow-y-auto pb-1"
      >
        {items.map(({ notice, leaving }) => (
          <NoticeBanner
            key={notice.id}
            notice={notice}
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
  leaving,
  offerPermission,
  onRequestPermission,
  onJump,
  onDismiss,
  onExpire,
}: {
  notice: Notice;
  leaving: boolean;
  offerPermission: boolean;
  onRequestPermission: () => void;
  onJump: () => void;
  onDismiss: () => void;
  onExpire: () => void;
}) {
  const persistent = isPersistentKind(notice.kind);
  const meta = stateMeta(notice.entry.state);
  /* The meta line is frozen at creation time on purpose — the banner reports
   * what was true when the event happened, so a live clock here would be
   * re-reading state the banner does not track. */
  const context = rowContext(notice.entry, notice.createdAt);
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
        "group relative shrink-0 rounded-lg border shadow-raised " +
        (leaving ? "notice-banner-leave " : "notice-banner-enter ") +
        KIND_FRAME[notice.kind]
      }
    >
      <button
        type="button"
        onClick={onJump}
        className="flex w-full items-start gap-2.5 rounded-lg px-3.5 py-3 text-left transition hover:brightness-[0.98]"
        title="跳转到该节点"
      >
        {/* The state badge stands in for a macOS notification's app icon:
            square, colored by state, and the one part of the banner readable
            at a glance from across the screen. */}
        <span
          className={
            "mt-px flex h-6 w-6 shrink-0 items-center justify-center rounded-md " +
            meta.chipBg +
            " " +
            meta.chipText
          }
          aria-hidden="true"
        >
          <meta.Icon />
        </span>

        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span
              className={
                "truncate text-[12.5px] font-semibold leading-5 " +
                KIND_TITLE[notice.kind]
              }
            >
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
              question being asked. */}
          {body ? (
            <span className="mt-0.5 block line-clamp-2 text-[11.5px] leading-[1.45] text-ink-muted">
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

      {/* Corner close, macOS-style: out of the text's way, and revealed on
        * hover so a rail at rest stays quiet. Transient banners leave on their
        * own, so only persistent ones offer it. */}
      {persistent ? (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="关闭此通知"
          title="关闭"
          className="absolute right-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-surface-raised/80 text-ink-subtle opacity-0 shadow-card transition hover:text-ink focus-visible:opacity-100 group-hover:opacity-100"
        >
          <svg
            viewBox="0 0 24 24"
            width="11"
            height="11"
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
