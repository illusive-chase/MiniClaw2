import { memo } from "react";
import type { NodeProps } from "reactflow";
import type { PlanspaceLaneData } from "../layout";

function PlanspaceLaneNodeImpl({ data }: NodeProps<PlanspaceLaneData>) {
  return (
    <div
      className="pointer-events-none rounded-md border"
      style={{
        width: data.width,
        height: data.height,
        background: data.color.bg,
        borderColor: data.color.border,
      }}
    >
      <div
        className="flex h-8 items-center gap-2 border-b px-3 text-[10px] font-medium uppercase tracking-[0.14em]"
        style={{ borderColor: data.color.border, color: data.color.text }}
      >
        <span
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{ background: data.color.accent }}
          aria-hidden="true"
        />
        <span className="truncate">{data.label}</span>
        <span className="font-mono opacity-70">{data.nodeCount} nodes</span>
      </div>
    </div>
  );
}

export const PlanspaceLaneNode = memo(PlanspaceLaneNodeImpl);
