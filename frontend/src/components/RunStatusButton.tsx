/* Run-status button: what the machine is doing right now.
 *
 * Split out of the notification bell, which was carrying two signals at once.
 * The bell answers "what happened while I was away" — an event count that only
 * the user can clear. This answers "what is in flight" — a state count that
 * falls on its own as work finishes. One number could not say both, and the
 * running signal had been compressed into a pulsing dot with no count at all.
 *
 * `waiting` appears here *and* in the bell's badge, deliberately. It is both
 * "this project has not finished" and "this needs me"; the two buttons ask
 * different questions and the same node can answer both.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import {
  CATEGORY_LABELS,
  activeStateLabel,
  badgeCountLabel,
  rowContext,
  runStatusEntries,
  sortActiveEntries,
  summarize,
} from "../activeNodes";
import { stateMeta } from "../canvas/nodes/stateMeta";
import type { ActiveNodeEntry } from "../types";

type Props = {
  enabled: boolean;
  entries: ActiveNodeEntry[];
  currentSessionId: string | null;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  onJump: (entry: ActiveNodeEntry) => void;
};

/* Panel gap below the header, matching NotificationBell's offset. */
const PANEL_TOP_GAP = 6;

export function RunStatusButton({
  enabled,
  entries,
  currentSessionId,
  open,
  onToggle,
  onClose,
  onJump,
}: Props) {
  const [panelTop, setPanelTop] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  const rows = sortActiveEntries(runStatusEntries(entries));
  const summary = summarize(rows);
  const count = rows.length;

  /* Durations tick off a clock rather than the poll, so an elapsed time
   * advances smoothly between pushes. Only while open — nothing else on
   * screen shows one. */
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!open) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [open]);

  const updatePanelTop = useCallback(() => {
    const trigger = buttonRef.current;
    if (!trigger) return;
    /* The panel spans the viewport, so only its vertical origin is measured:
     * the header's own height is not a constant this component should encode. */
    const header = trigger.closest("header");
    const rect = (header ?? trigger).getBoundingClientRect();
    setPanelTop(rect.bottom + (header ? 0 : PANEL_TOP_GAP));
  }, []);

  /* Measure before paint on the frame the panel opens, so it never renders
   * once at the wrong offset. */
  useLayoutEffect(() => {
    if (open) updatePanelTop();
  }, [open, updatePanelTop]);

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
      onClose();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onOutside, true);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", updatePanelTop);
    window.addEventListener("scroll", updatePanelTop, true);
    return () => {
      document.removeEventListener("mousedown", onOutside, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("resize", updatePanelTop);
      window.removeEventListener("scroll", updatePanelTop, true);
    };
  }, [onClose, open, updatePanelTop]);

  if (!enabled) return null;

  const parts = [
    summary.running > 0 ? `${summary.running} 在跑` : "",
    summary.queued > 0 ? `${summary.queued} 排队` : "",
    summary.waiting > 0 ? `${summary.waiting} 等我` : "",
  ].filter(Boolean);

  const ariaLabel =
    count === 0 ? "运行状况：当前没有节点在跑" : `运行状况：${parts.join(" · ")}`;

  return (
    <div ref={containerRef} className="relative shrink-0">
      <button
        ref={buttonRef}
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={ariaLabel}
        title={ariaLabel}
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
          <path d="M12 3a9 9 0 1 1-9 9" />
          <path d="M12 8v4l3 2" />
        </svg>
        {/* One tone, unlike the bell's three. This count is not about urgency
          * — it is about the machine being busy, and busy has one meaning. */}
        {count > 0 ? (
          <span
            aria-hidden="true"
            className="pulse-slow absolute -right-1 -top-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-brand px-1 font-mono text-[9px] font-semibold leading-none text-white tabular-nums shadow-sm"
          >
            {badgeCountLabel(count)}
          </span>
        ) : null}
      </button>

      {open && panelTop !== null ? (
        <div
          ref={panelRef}
          role="dialog"
          aria-label="运行状况"
          className="active-nodes-panel-enter fixed left-0 right-0 z-50 overflow-hidden border-y border-line bg-surface-raised font-sans shadow-modal"
          style={{ top: panelTop }}
        >
          <div className="flex items-center justify-between gap-4 border-b border-line px-4 py-2.5">
            <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              运行状况
              {count > 0 ? ` · ${parts.join(" · ")}` : " · 空闲"}
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-line bg-surface px-2 py-0.5 font-mono text-[10px] text-ink-subtle transition hover:border-line-strong hover:text-ink"
            >
              Esc
            </button>
          </div>

          <div className="max-h-[50vh] overflow-y-auto">
            {rows.length === 0 ? (
              <div className="px-4 py-6 text-center text-[11px] text-ink-muted">
                当前没有节点在跑
              </div>
            ) : (
              rows.map((entry) => (
                <RunStatusRow
                  key={`${entry.project_id}:${entry.node_id}`}
                  entry={entry}
                  isCurrent={entry.project_id === currentSessionId}
                  now={now}
                  onJump={() => {
                    onClose();
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

function RunStatusRow({
  entry,
  isCurrent,
  now,
  onJump,
}: {
  entry: ActiveNodeEntry;
  isCurrent: boolean;
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
      className={
        "flex w-full items-center gap-3 border-b border-line px-4 py-2 text-left transition last:border-b-0 hover:bg-surface-sunken " +
        (isCurrent ? "opacity-60" : "")
      }
    >
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
