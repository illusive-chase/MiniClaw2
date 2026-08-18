/* Cross-project status bar: a hairline that colors when something needs
 * attention elsewhere, expanding into a list of those nodes.
 *
 * The hairline deliberately counts only nodes *outside* the current project.
 * What is happening here is already told by the canvas, the node state
 * colors, and the pending-gate banner; repeating it would leave the bar amber
 * while the user is actively handling the thing it is pointing at, which
 * trains them to ignore it. The bar means exactly "there is something
 * elsewhere".
 */

import { useEffect, useMemo, useRef, useState } from "react";

import {
  activeStateLabel,
  barTone,
  CATEGORY_LABELS,
  isHumanBlocked,
  readWaitingOnly,
  rowContext,
  sortActiveEntries,
  summarize,
  useActiveNodes,
  writeWaitingOnly,
} from "../activeNodes";
import { stateMeta } from "../canvas/nodes/stateMeta";
import type { ActiveNodeEntry } from "../types";

type Props = {
  enabled: boolean;
  currentSessionId: string | null;
  onJump: (entry: ActiveNodeEntry) => void;
};

const TONE_CLASS: Record<string, string> = {
  waiting: "bg-state-waiting pulse-slow",
  running: "bg-state-running",
  error: "bg-state-error",
};

export function ActiveNodesBar({ enabled, currentSessionId, onJump }: Props) {
  const entries = useActiveNodes(enabled);
  const [open, setOpen] = useState(false);
  const [waitingOnly, setWaitingOnly] = useState(readWaitingOnly);
  const containerRef = useRef<HTMLDivElement | null>(null);

  /* Elapsed times are derived from a clock tick rather than from the poll, so
   * "已跑 4m12s" advances smoothly between fetches. Only ticks while the panel
   * is open — nothing else displays a duration. */
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!open) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [open]);

  /* The hairline's own signal excludes the current project (see file header),
   * but the expanded list keeps those rows so "how much is running in total"
   * stays answerable. */
  const elsewhere = useMemo(
    () => entries.filter((entry) => entry.project_id !== currentSessionId),
    [entries, currentSessionId],
  );
  const summary = useMemo(() => summarize(elsewhere), [elsewhere]);
  const tone = barTone(summary);

  const rows = useMemo(() => {
    const filtered = waitingOnly ? entries.filter(isHumanBlocked) : entries;
    return sortActiveEntries(filtered);
  }, [entries, waitingOnly]);

  useEffect(() => {
    if (!open) return;
    /* Capture phase: React Flow's d3-zoom calls stopPropagation on canvas
     * events, so a bubble-phase listener can miss clicks that land on the
     * graph and leave the panel stuck open. */
    const onOutside = (event: MouseEvent) => {
      if (!containerRef.current) return;
      if (!containerRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onOutside, true);
    return () => document.removeEventListener("mousedown", onOutside, true);
  }, [open]);

  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        return;
      }
      if (event.key.toLowerCase() !== "k" || !(event.metaKey || event.ctrlKey)) return;
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName ?? "";
      if (["INPUT", "TEXTAREA", "SELECT"].includes(tag) || target?.isContentEditable) {
        return;
      }
      event.preventDefault();
      setOpen((value) => !value);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [enabled]);

  if (!enabled) return null;

  const countLabel = [
    summary.waiting > 0 ? `${summary.waiting} 等我` : "",
    summary.error > 0 ? `${summary.error} 出错` : "",
    summary.running > 0 ? `${summary.running} 在跑` : "",
    summary.queued > 0 ? `${summary.queued} 排队` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div ref={containerRef} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label={
          countLabel ? `其他项目：${countLabel}` : "其他项目没有正在执行的节点"
        }
        title={
          countLabel
            ? `其他项目：${countLabel}（⌘K 展开）`
            : "其他项目没有正在执行的节点（⌘K 展开）"
        }
        className="group relative block h-[2px] w-full cursor-pointer"
      >
        <span
          className={
            "block h-full w-full transition-colors duration-200 " +
            (tone ? TONE_CLASS[tone] : "bg-line")
          }
        />
        {countLabel ? (
          <span className="absolute right-3 top-[2px] rounded-b bg-surface-raised px-1.5 font-mono text-[10px] tabular-nums text-ink-muted shadow-sm">
            {countLabel}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="active-nodes-panel-enter absolute inset-x-0 top-full z-30 overflow-hidden rounded-b-xl border-x border-b border-line bg-surface-raised shadow-modal">
          <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
            <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              跨项目{countLabel ? ` · ${countLabel}` : ""}
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  const next = !waitingOnly;
                  setWaitingOnly(next);
                  writeWaitingOnly(next);
                }}
                className={
                  "rounded border px-2 py-0.5 text-[11px] transition " +
                  (waitingOnly
                    ? "border-state-waiting/50 bg-state-waiting-soft text-state-waiting"
                    : "border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink")
                }
              >
                只看等我
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded border border-line bg-surface px-2 py-0.5 font-mono text-[10px] text-ink-subtle transition hover:border-line-strong hover:text-ink"
              >
                Esc
              </button>
            </div>
          </div>

          <div className="max-h-[50vh] overflow-y-auto">
            {rows.length === 0 ? (
              <div className="px-4 py-6 text-center text-[11px] text-ink-muted">
                {waitingOnly ? "没有等待你的节点" : "没有正在执行的节点"}
              </div>
            ) : (
              rows.map((entry) => (
                <ActiveNodeRow
                  key={`${entry.project_id}:${entry.node_id}`}
                  entry={entry}
                  isCurrent={entry.project_id === currentSessionId}
                  now={now}
                  onJump={() => {
                    setOpen(false);
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

function ActiveNodeRow({
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
