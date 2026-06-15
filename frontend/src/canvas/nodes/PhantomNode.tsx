import { memo, useEffect, useRef, useState } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { PhantomNodeData } from "../layout";

export type PhantomSubmit = {
  prompt: string;
  resumeFromNodeId: string | null;
};

export type PhantomPlanspaceOption = {
  id: string;
  label: string;
};

export type PhantomNodeContext = {
  onSubmit: (payload: PhantomSubmit) => void;
  onDismiss: () => void;
  /** clear resume source (convert into fresh-start) */
  onClearResume: () => void;
  disabled: boolean;
  /** Kept for compatibility while the composer no longer exposes cross-lane loads. */
  planspaceOptions: PhantomPlanspaceOption[];
  /** Planspace id the active binding will inject by default. */
  activePlanspaceId: string | null;
};

/**
 * Phantom composer: a dashed-outline node that materializes where the next
 * run will appear. Authoring is direct — no modal. Submit promotes the
 * phantom into a real running tile at the same position.
 *
 * The composer reads its action context from a module-level singleton because
 * React Flow's `data` prop isn't ergonomic for callbacks that change on every
 * App.tsx render.
 */
function PhantomNodeImpl({ data, selected }: NodeProps<PhantomNodeData>) {
  const ctx = phantomContext;
  const [prompt, setPrompt] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const disabled = data.disabled || ctx.disabled;

  useEffect(() => {
    /* Focus the input as soon as the phantom appears. */
    const timer = window.setTimeout(() => textareaRef.current?.focus(), 30);
    return () => window.clearTimeout(timer);
  }, []);

  const canSubmit = prompt.trim().length > 0 && !disabled;

  const submit = () => {
    const latest = phantomContext;
    if (prompt.trim().length === 0 || data.disabled || latest.disabled) return;
    latest.onSubmit({
      prompt: prompt.trim(),
      resumeFromNodeId: data.resumeFromNodeId,
    });
  };

  return (
    <div
      className={
        "relative w-[260px] select-none rounded-lg border-2 border-dashed bg-surface-raised/70 px-3 py-2.5 shadow-card transition " +
        (selected ? "border-brand" : "border-line-strong")
      }
    >
      {/* resume-from chip */}
      {data.resumeFromLabel && (
        <div className="mb-2 flex items-center justify-between gap-1.5 rounded border border-brand/30 bg-brand-soft px-2 py-0.5 text-[10px] text-brand-ink">
          <span className="truncate" title={data.resumeFromLabel}>
            ↻ continuing from "{data.resumeFromLabel}"
          </span>
          <button
            type="button"
            onClick={ctx.onClearResume}
            className="rounded px-1 text-[10px] text-brand-ink/70 hover:bg-surface-raised hover:text-brand-ink"
            title="Clear resume source"
          >
            ×
          </button>
        </div>
      )}

      <textarea
        ref={textareaRef}
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            submit();
          }
        }}
        rows={Math.min(8, Math.max(2, prompt.split("\n").length))}
        placeholder={
          data.resumeFromNodeId
            ? "What should the agent do next?"
            : "What should the agent do?"
        }
        disabled={disabled}
        className="nodrag w-full resize-none rounded-md bg-transparent text-[13px] leading-relaxed text-ink-strong placeholder:text-ink-subtle focus:outline-none"
      />

      <div className="mt-2 flex items-center justify-between text-[10px] text-ink-subtle">
        <span>⌘/Ctrl + Enter to launch · click outside to dismiss</span>
        <button
          type="button"
          onClick={submit}
          disabled={!canSubmit}
          className="rounded-md bg-brand px-2.5 py-1 text-[11px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:cursor-not-allowed disabled:opacity-40"
        >
          Launch
        </button>
      </div>

      <Handle
        type="target"
        position={Position.Left}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
    </div>
  );
}

export const PhantomNode = memo(PhantomNodeImpl);

/* Module-level singleton context: App.tsx writes the handler set, the
 * phantom reads them on render. Avoids stale closures from React Flow
 * memoizing the custom-node component. */
let phantomContext: PhantomNodeContext = {
  onSubmit: () => {},
  onDismiss: () => {},
  onClearResume: () => {},
  disabled: false,
  planspaceOptions: [],
  activePlanspaceId: null,
};

export function setPhantomContext(ctx: PhantomNodeContext): void {
  phantomContext = ctx;
}
