import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { ErrorTerminalData } from "../layout";

/**
 * Error terminal: a small red-edged downstream node that surfaces the failing
 * run's error text on the canvas itself, so a retry (resume edge from the
 * failed run's parent) reads as a visible loop instead of a banner inside a
 * collapsed summary tab.
 */
function ErrorTerminalNodeImpl({ data, selected }: NodeProps<ErrorTerminalData>) {
  const message = oneLine(data.message);
  return (
    <div
      title={data.message}
      className={
        "relative w-[180px] select-none rounded-md border bg-state-error-soft/60 px-2.5 py-1.5 shadow-card transition " +
        (selected
          ? "border-state-error ring-2 ring-state-error ring-offset-2 ring-offset-surface-sunken"
          : "border-state-error/60 hover:border-state-error hover:ring-2 hover:ring-state-error/35 hover:ring-offset-2 hover:ring-offset-surface-sunken")
      }
    >
      <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.14em] text-state-error">
        <CrossGlyph />
        <span>error</span>
      </div>
      <div className="line-clamp-3 pt-1 text-[11.5px] leading-snug text-state-error">
        {message}
      </div>

      <Handle
        type="target"
        position={Position.Left}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
    </div>
  );
}

export const ErrorTerminalNode = memo(ErrorTerminalNodeImpl);

function CrossGlyph() {
  return (
    <svg
      viewBox="0 0 10 10"
      width="10"
      height="10"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M2 2 8 8M8 2 2 8" />
    </svg>
  );
}

function oneLine(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}
