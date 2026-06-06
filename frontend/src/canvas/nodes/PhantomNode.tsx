import { memo, useEffect, useRef, useState } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { PhantomNodeData } from "../layout";

type OutputKind = "freeform" | "summary" | "interface" | "review_brief";

export type PhantomSubmit = {
  prompt: string;
  resumeFromNodeId: string | null;
  outputKind: OutputKind;
};

export type PhantomNodeContext = {
  onSubmit: (payload: PhantomSubmit) => void;
  onDismiss: () => void;
  /** clear resume source (convert into fresh-start) */
  onClearResume: () => void;
  disabled: boolean;
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
  const [outputKind, setOutputKind] = useState<OutputKind>("summary");
  const [showMore, setShowMore] = useState(false);
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
      outputKind,
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

      {/* intent chip row */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <IntentChip
          label="Explore"
          active={outputKind === "freeform"}
          onClick={() => setOutputKind("freeform")}
        />
        <IntentChip
          label="Build & summarize"
          active={outputKind === "summary"}
          onClick={() => setOutputKind("summary")}
        />
        <IntentChip
          label="Hand off for review"
          active={outputKind === "review_brief"}
          onClick={() => setOutputKind("review_brief")}
        />
        <button
          type="button"
          onClick={() => setShowMore((v) => !v)}
          className="ml-auto rounded px-1.5 py-0.5 text-[10px] text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
          title="More intents"
        >
          ⋯
        </button>
      </div>

      {showMore && (
        <div className="mt-1 flex flex-wrap gap-1.5 border-t border-line pt-1.5">
          <IntentChip
            label="Interface (JSON)"
            active={outputKind === "interface"}
            onClick={() => setOutputKind("interface")}
          />
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
};

export function setPhantomContext(ctx: PhantomNodeContext): void {
  phantomContext = ctx;
}

function IntentChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "nodrag rounded-full border px-2 py-0.5 text-[10.5px] transition " +
        (active
          ? "border-brand bg-brand-soft text-brand-ink"
          : "border-line text-ink-muted hover:border-line-strong hover:bg-surface-sunken hover:text-ink")
      }
    >
      {label}
    </button>
  );
}
