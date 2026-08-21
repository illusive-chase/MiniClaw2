/* Cross-project notification bell: unread events, expanding into a full-width
 * feed.
 *
 * The badge counts *unread events* — things that happened which this device has
 * not shown the user yet. It counts only events worth acknowledging once
 * (blocked / failed / finished); running and queued nodes belong to the
 * run-status button beside it, where a count that rises and falls with the
 * machine is the right shape. A badge that is never zero on a busy workspace
 * teaches the user to ignore it.
 *
 * Every project counts, including the one on screen. Unread is about whether a
 * person saw an event, and an event in the current project can be missed just
 * as easily — the user may have been in another lane, reading a panel, or away
 * from the keyboard.
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  activeStateLabel,
  badgeCountLabel,
  badgeTone,
  CATEGORY_LABELS,
  isUnread,
  isVerticallyVisible,
  notificationKey,
  readUnreadOnly,
  rowContext,
  sortFeed,
  summarize,
  summaryParts,
  unreadEntries,
  withinTerminalWindow,
  type ActiveNodesFeed,
  type ReadKeysController,
  writeUnreadOnly,
} from "../activeNodes";
import { stateMeta } from "../canvas/nodes/stateMeta";
import { badgeCountable } from "../notices";
import type { ActiveNodeEntry } from "../types";

type Props = {
  enabled: boolean;
  feed: ActiveNodesFeed;
  currentSessionId: string | null;
  /* Read state is owned outside this component: the banner rail settles the
   * same keys, and two owners would let one surface show as unread what the
   * other already acknowledged. */
  readKeysController: ReadKeysController;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  onJump: (entry: ActiveNodeEntry) => void;
};

const BADGE_TONE_CLASS: Record<string, string> = {
  waiting: "bg-state-waiting text-white",
  error: "bg-state-error text-white",
  done: "bg-state-done text-white",
};

/* Panel gap below the header, matching GitWorkspaceStatus's popover offset. */
const PANEL_TOP_GAP = 6;

export function NotificationBell({
  enabled,
  feed,
  currentSessionId,
  readKeysController,
  open,
  onToggle,
  onClose,
  onJump,
}: Props) {
  const { entries } = feed;
  const { readKeys, markRead, markAllRead } = readKeysController;
  const [unreadOnly, setUnreadOnly] = useState(readUnreadOnly);
  const [panelTop, setPanelTop] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);

  /* Elapsed times tick off a clock rather than the poll, so "已跑 4m12s"
   * advances smoothly between fetches. Only while the panel is open — nothing
   * else on screen displays a duration. */
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!open) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [open]);

  /* The server applies the same window when it builds a snapshot, but a
   * snapshot is only re-fetched on reconnect or tab focus. Re-applying it here
   * stops a long-lived foreground tab from accumulating rows the server has
   * already aged out.
   *
   * The clock is sampled here rather than read from the ticking `now` above,
   * which is frozen while the panel is closed and would leave the badge
   * counting an expired row. Sampling in the memo re-reads it on open and on
   * every feed change — a pushed transition or a re-fetch — so the only stale
   * case left is a tab where nothing at all has happened for eight hours, and
   * opening the panel corrects that. No timer runs while closed for this. */
  const visibleEntries = useMemo(
    () => withinTerminalWindow(entries, Date.now()),
    [entries, open],
  );

  /* Two collections, because the badge and the panel answer different
   * questions and conflating them made the panel contradict itself.
   *
   * The badge counts only events worth one acknowledgement each (D13): a
   * workspace with anything running would otherwise carry a permanently
   * non-zero badge, which conveys nothing — running and queued live on the
   * run-status button instead.
   *
   * The panel lists *every* row, so its header text and its "mark all read"
   * button must count every unread row. Driving those off the badge's filtered
   * set let the header say "无未读" while unread running rows sat underneath
   * it, with a bulk action disabled and unable to clear them. */
  const unread = useMemo(
    () => unreadEntries(visibleEntries, readKeys),
    [visibleEntries, readKeys],
  );
  const badgeUnread = useMemo(() => badgeCountable(unread), [unread]);
  const tone = badgeTone(badgeUnread);
  const badgeParts = useMemo(
    () => summaryParts(summarize(badgeUnread)),
    [badgeUnread],
  );
  const unreadParts = useMemo(() => summaryParts(summarize(unread)), [unread]);

  const rows = useMemo(() => {
    const filtered = unreadOnly
      ? visibleEntries.filter((entry) => isUnread(entry, readKeys))
      : visibleEntries;
    return sortFeed(filtered);
  }, [visibleEntries, readKeys, unreadOnly]);

  /* Accumulate only rows that actually intersected the scrolling viewport.
   * The feed can be taller than 50vh, and it keeps polling while open, so the
   * rendered `rows` array includes both off-screen rows and late arrivals the
   * user never saw. */
  const visibleDuringOpenRef = useRef<Set<string>>(new Set());
  const captureVisibleRows = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const viewportBounds = viewport.getBoundingClientRect();
    for (const row of viewport.querySelectorAll<HTMLElement>(
      "[data-notification-key]",
    )) {
      if (!isVerticallyVisible(row.getBoundingClientRect(), viewportBounds)) continue;
      const key = row.dataset.notificationKey;
      if (key) visibleDuringOpenRef.current.add(key);
    }
  }, []);

  /* Layout timing means measurements happen after every feed/filter render,
   * before the browser can accept the next click. Scroll events add each new
   * screenful as the user moves through the list. */
  useLayoutEffect(() => {
    if (open) captureVisibleRows();
  }, [captureVisibleRows, open, rows]);

  /* Settle on the open→closed transition rather than inside the click handler.
   * The panel can also be closed from outside — opening the run-status panel
   * closes this one — and rows the user read during that visit must count as
   * read however the panel went away. */
  const settleSeenRows = useCallback(() => {
    const seen = visibleDuringOpenRef.current;
    if (seen.size === 0) return;
    visibleDuringOpenRef.current = new Set();
    markRead(seen);
  }, [markRead]);

  const wasOpenRef = useRef(open);
  useEffect(() => {
    if (wasOpenRef.current && !open) settleSeenRows();
    wasOpenRef.current = open;
  }, [open, settleSeenRows]);

  const closeAndSettle = useCallback(() => {
    captureVisibleRows();
    onClose();
  }, [captureVisibleRows, onClose]);

  const updatePanelTop = useCallback(() => {
    const trigger = buttonRef.current;
    if (!trigger) return;
    /* The panel spans the viewport, so only its vertical origin is measured:
     * the header's own height is not a constant this component should encode. */
    const header = trigger.closest("header");
    const rect = (header ?? trigger).getBoundingClientRect();
    setPanelTop(rect.bottom + (header ? 0 : PANEL_TOP_GAP));
  }, []);

  useEffect(() => {
    if (!open) return;
    /* Capture phase: React Flow's d3-zoom calls stopPropagation on canvas
     * events, so a bubble-phase listener can miss clicks that land on the
     * graph and leave the panel stuck open. */
    const onOutside = (event: MouseEvent) => {
      const root = containerRef.current;
      const panel = panelRef.current;
      const target = event.target as Node;
      if (root?.contains(target) || panel?.contains(target)) return;
      closeAndSettle();
    };
    const reposition = () => {
      updatePanelTop();
      captureVisibleRows();
    };
    document.addEventListener("mousedown", onOutside, true);
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => {
      document.removeEventListener("mousedown", onOutside, true);
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [captureVisibleRows, closeAndSettle, open, updatePanelTop]);

  const toggle = useCallback(() => {
    if (open) {
      closeAndSettle();
      return;
    }
    updatePanelTop();
    visibleDuringOpenRef.current = new Set();
    onToggle();
  }, [closeAndSettle, onToggle, open, updatePanelTop]);

  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (open) closeAndSettle();
        return;
      }
      if (event.key.toLowerCase() !== "k" || !(event.metaKey || event.ctrlKey)) return;
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName ?? "";
      if (["INPUT", "TEXTAREA", "SELECT"].includes(tag) || target?.isContentEditable) {
        return;
      }
      event.preventDefault();
      toggle();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [closeAndSettle, enabled, open, toggle]);

  if (!enabled) return null;

  /* Spelled out because a screen reader gets neither the badge's color nor its
   * position. The badge's own count, so the label matches the glyph. */
  const ariaLabel =
    badgeUnread.length === 0
      ? "通知：无未读"
      : `通知：${badgeUnread.length} 条未读（${badgeParts.join(" · ")}）`;

  return (
    <div ref={containerRef} className="relative shrink-0">
      <button
        ref={buttonRef}
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={ariaLabel}
        title={`${ariaLabel}（⌘K 展开）`}
        className={
          "relative inline-flex h-8 w-8 items-center justify-center rounded-md border transition " +
          (open
            ? "border-line-strong bg-surface-sunken text-ink"
            : "border-line bg-surface text-ink-muted hover:border-line-strong hover:bg-surface-sunken hover:text-ink")
        }
      >
        <svg
          viewBox="0 0 24 24"
          width="17"
          height="17"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.7 21a2 2 0 0 1-3.4 0" />
        </svg>
        {tone ? (
          <span
            aria-hidden="true"
            className={
              "absolute -right-1 -top-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 font-mono text-[9px] font-semibold leading-none tabular-nums shadow-sm " +
              BADGE_TONE_CLASS[tone]
            }
          >
            {badgeCountLabel(badgeUnread.length)}
          </span>
        ) : null}
      </button>

      {open && panelTop !== null ? (
        <div
          ref={panelRef}
          role="dialog"
          aria-label="通知"
          className="active-nodes-panel-enter fixed left-0 right-0 z-50 overflow-hidden border-y border-line bg-surface-raised font-sans shadow-modal"
          style={{ top: panelTop }}
        >
          <div className="flex items-center justify-between gap-4 border-b border-line px-4 py-2.5">
            <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              通知
              {unread.length > 0 ? ` · ${unreadParts.join(" · ")}` : " · 无未读"}
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  const next = !unreadOnly;
                  setUnreadOnly(next);
                  writeUnreadOnly(next);
                }}
                className={
                  "rounded border px-2 py-0.5 text-[11px] transition " +
                  (unreadOnly
                    ? "border-brand/50 bg-brand/10 text-ink-strong"
                    : "border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink")
                }
              >
                只看未读
              </button>
              <button
                type="button"
                onClick={markAllRead}
                disabled={unread.length === 0}
                className="rounded border border-line bg-surface px-2 py-0.5 text-[11px] text-ink-muted transition hover:border-line-strong hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
              >
                全部标为已读
              </button>
              <button
                type="button"
                onClick={closeAndSettle}
                className="rounded border border-line bg-surface px-2 py-0.5 font-mono text-[10px] text-ink-subtle transition hover:border-line-strong hover:text-ink"
              >
                Esc
              </button>
            </div>
          </div>

          <div
            ref={viewportRef}
            onScroll={captureVisibleRows}
            className="max-h-[50vh] overflow-y-auto"
          >
            {rows.length === 0 ? (
              <div className="px-4 py-6 text-center text-[11px] text-ink-muted">
                {unreadOnly ? "没有未读通知" : "最近没有节点活动"}
              </div>
            ) : (
              rows.map((entry) => (
                <NotificationRow
                  key={`${entry.project_id}:${entry.node_id}:${entry.state}`}
                  entry={entry}
                  isCurrent={entry.project_id === currentSessionId}
                  unread={isUnread(entry, readKeys)}
                  notificationKeyValue={notificationKey(entry)}
                  now={now}
                  onJump={() => {
                    closeAndSettle();
                    onJump(entry);
                  }}
                />
              ))
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function NotificationRow({
  entry,
  isCurrent,
  unread,
  notificationKeyValue,
  now,
  onJump,
}: {
  entry: ActiveNodeEntry;
  isCurrent: boolean;
  unread: boolean;
  notificationKeyValue: string;
  now: number;
  onJump: () => void;
}) {
  /* stateMeta is the single source of node state color; re-deriving chip
   * colors here would fork the moment those tokens change. */
  const meta = stateMeta(entry.state);
  const context = rowContext(entry, now);
  return (
    <button
      type="button"
      onClick={onJump}
      data-notification-key={notificationKeyValue}
      className={
        "relative flex w-full items-center gap-3 border-b border-line px-4 py-2 text-left transition last:border-b-0 hover:bg-surface-sunken " +
        (isCurrent ? "opacity-60" : "")
      }
    >
      {/* A left rule rather than a row tint: `isCurrent` already spends
        * opacity, and stacking two transparencies yields four greys nobody
        * can tell apart. */}
      {unread ? (
        <span
          aria-hidden="true"
          className="absolute inset-y-0 left-0 w-[2px] bg-brand"
        />
      ) : null}
      <span
        className={
          "inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-[0.12em] " +
          meta.chipBg +
          " " +
          meta.chipText
        }
      >
        {activeStateLabel(entry.state)}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="truncate text-[12.5px] font-medium text-ink-strong">
            {entry.project_name || "未命名项目"}
          </span>
          {isCurrent ? (
            <span className="shrink-0 text-[10px] text-ink-subtle">←当前</span>
          ) : null}
          <span className="shrink-0 font-mono text-[10px] text-ink-subtle">
            {entry.node_id.slice(0, 8)}
          </span>
        </span>
        <span className="flex items-center gap-2 text-[11px] text-ink-muted">
          {entry.category ? (
            <span className="shrink-0">{CATEGORY_LABELS[entry.category]}</span>
          ) : null}
          {entry.planspace_title ? (
            <>
              <span className="text-line-strong">·</span>
              <span className="truncate">{entry.planspace_title}</span>
            </>
          ) : null}
          {entry.label ? (
            <>
              <span className="text-line-strong">·</span>
              <span className="truncate">{entry.label}</span>
            </>
          ) : null}
        </span>
      </span>
      {context ? (
        <span className="shrink-0 text-[11px] text-ink-muted">{context}</span>
      ) : null}
    </button>
  );
}
