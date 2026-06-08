import { memo } from "react";
import type { NodeProps } from "reactflow";
import type { PlanspaceLaneData } from "../layout";

function PlanspaceLaneNodeImpl({ data }: NodeProps<PlanspaceLaneData>) {
  const ctx = planspaceLaneContext;
  return (
    <div
      className="rounded-md border"
      style={{
        width: data.width,
        height: data.height,
        background: data.color.bg,
        borderColor: data.color.border,
        pointerEvents: "none",
      }}
    >
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          ctx.onSelectPlanspace(data.planspaceId);
        }}
        className="nodrag flex h-8 w-full items-center gap-2 border-b px-3 text-left text-[10px] font-medium uppercase tracking-[0.14em] transition hover:bg-surface-raised/40"
        style={{
          borderColor: data.color.border,
          color: data.color.text,
          pointerEvents: "auto",
        }}
        title="Open planspace status panel"
      >
        <span
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{ background: data.color.accent }}
          aria-hidden="true"
        />
        <span className="truncate">{data.label}</span>
        <span className="font-mono opacity-70">{data.nodeCount} nodes</span>
      </button>
    </div>
  );
}

export const PlanspaceLaneNode = memo(PlanspaceLaneNodeImpl);

/* Module-level singleton: App.tsx wires the selection callback so the lane
 * header can route clicks without prop-drilling through React Flow data. */
export type PlanspaceLaneContext = {
  onSelectPlanspace: (planspaceId: string) => void;
};

let planspaceLaneContext: PlanspaceLaneContext = {
  onSelectPlanspace: () => {},
};

export function setPlanspaceLaneContext(ctx: PlanspaceLaneContext): void {
  planspaceLaneContext = ctx;
}
