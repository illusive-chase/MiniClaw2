import { memo, useEffect, useRef, useState } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { PhantomNodeData } from "../layout";

export type PhantomSubmit = {
  prompt: string;
  resumeFromNodeId: string | null;
  needsReview: boolean;
  extraPlanspaceLoads: string[];
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
  /** Other planspaces the user can pull context from on this run. */
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
  const [needsReview, setNeedsReview] = useState(false);
  const [extraLoads, setExtraLoads] = useState<string[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
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
      needsReview,
      extraPlanspaceLoads: extraLoads,
    });
  };

  const availableLoads = ctx.planspaceOptions.filter(
    (opt) => opt.id !== ctx.activePlanspaceId && !extraLoads.includes(opt.id),
  );
  const labelById = new Map(ctx.planspaceOptions.map((o) => [o.id, o.label]));

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
          } else if (e.key === "Escape") {
            e.preventDefault();
            ctx.onDismiss();
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

      <label className="nodrag mt-2 flex items-center justify-between gap-3 rounded border border-line bg-surface-sunken/60 px-2 py-1.5 text-[11px] text-ink">
        <span>Needs review</span>
        <input
          type="checkbox"
          checked={needsReview}
          onChange={(e) => setNeedsReview(e.target.checked)}
          className="h-3.5 w-3.5 accent-brand"
        />
      </label>

      {(extraLoads.length > 0 || availableLoads.length > 0) && (
        <div className="nodrag mt-2 space-y-1">
          {extraLoads.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {extraLoads.map((id) => (
                <span
                  key={id}
                  className="inline-flex items-center gap-1 rounded border border-line bg-surface-sunken px-1.5 py-0.5 text-[10px] text-ink-muted"
                >
                  <span>↗ {labelById.get(id) ?? id}</span>
                  <button
                    type="button"
                    onClick={() =>
                      setExtraLoads((prev) => prev.filter((x) => x !== id))
                    }
                    className="text-ink-subtle hover:text-state-error"
                    title="Remove"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          {availableLoads.length > 0 && (
            <div className="relative">
              <button
                type="button"
                onClick={() => setPickerOpen((o) => !o)}
                className="rounded border border-dashed border-line bg-surface px-2 py-0.5 text-[10px] text-ink-muted hover:border-brand hover:text-ink-strong"
              >
                + load from another direction
              </button>
              {pickerOpen && (
                <div className="absolute z-20 mt-1 max-h-48 w-full overflow-auto rounded-md border border-line bg-surface-raised text-[11px] shadow-card">
                  {availableLoads.map((opt) => (
                    <button
                      key={opt.id}
                      type="button"
                      onClick={() => {
                        setExtraLoads((prev) => [...prev, opt.id]);
                        setPickerOpen(false);
                      }}
                      className="block w-full px-2 py-1 text-left hover:bg-surface-sunken"
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="mt-2 flex items-center justify-between text-[10px] text-ink-subtle">
        <span>⌘/Ctrl + Enter to launch · Esc to dismiss</span>
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
