import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { OpNodeData } from "../layout";

/**
 * Op tile: small chevron rendered on the timeline lane between a parent and a
 * child. v1 places it as a free-standing tiny tile; a later refinement (PRD
 * §3.1) will inline it as a chevron *on* the timeline edge.
 */
function OpNodeImpl({ data, selected }: NodeProps<OpNodeData>) {
  const { node } = data;
  const opKind = node.op_kind ?? "op";
  const sha = (node.commit_after ?? node.commit_before ?? "").slice(0, 7);
  const summary = (node.summary || "").replace(/\s+/g, " ").trim();

  return (
    <div
      title={`Op · ${opKind}${sha ? ` · ${sha}` : ""}${summary ? `\n${summary}` : ""}\n${node.id}`}
      className={
        "relative flex h-[48px] w-[96px] select-none items-center justify-center rounded-md border bg-surface-raised text-[10px] font-medium uppercase tracking-[0.12em] text-ink-muted shadow-card transition " +
        (selected
          ? "border-brand ring-2 ring-brand ring-offset-2 ring-offset-surface-sunken"
          : "border-line hover:border-line-strong hover:text-ink")
      }
    >
      <span className="absolute -left-1.5 top-1/2 -translate-y-1/2 text-base text-ink-subtle">
        ›
      </span>
      <div className="flex flex-col items-center leading-tight">
        <span className="font-display tracking-normal text-ink">{opKind}</span>
        {sha && (
          <span className="font-mono text-[9px] tracking-normal text-ink-subtle">
            {sha}
          </span>
        )}
      </div>
      <span className="absolute -right-1.5 top-1/2 -translate-y-1/2 text-base text-ink-subtle">
        ›
      </span>

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

export const OpNode = memo(OpNodeImpl);
