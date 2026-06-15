import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { ScenarioFutureData } from "../layout";

function ScenarioFutureNodeImpl({ data }: NodeProps<ScenarioFutureData>) {
  const { spec, index } = data;
  const tone =
    spec.category === "review"
      ? "border-state-review/35 bg-state-review-soft/45 text-state-review"
      : spec.category === "planning"
        ? "border-brand/35 bg-brand-soft/45 text-brand-ink"
        : "border-line-strong bg-surface-raised/70 text-ink-muted";
  const label =
    spec.category === "review"
      ? spec.subtype === "human_interact_review"
        ? "human review"
        : "review"
      : spec.category;

  return (
    <div
      className="relative w-[224px] select-none rounded-lg border-2 border-dashed border-line-strong bg-surface-raised/55 px-3 py-2.5 text-left opacity-80 shadow-card"
      title={`Scenario step ${spec.id}`}
    >
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className={"rounded border px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-[0.12em] " + tone}>
          {label}
        </span>
        <span className="font-mono text-[10px] text-ink-subtle">
          {index + 1} · next
        </span>
      </div>
      <div className="line-clamp-3 text-[12px] leading-snug text-ink">
        {spec.prompt_preview || spec.id}
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-ink-subtle">
        <span className="font-mono">{spec.id}</span>
        {spec.resume_from ? (
          <span className="font-mono">↻ {spec.resume_from}</span>
        ) : spec.review_source ? (
          <span className="font-mono">reviews {spec.review_source}</span>
        ) : null}
      </div>
      <Handle
        type="target"
        position={Position.Left}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
      <Handle
        type="source"
        position={Position.Right}
        className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
      />
    </div>
  );
}

export const ScenarioFutureNode = memo(ScenarioFutureNodeImpl);
