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
        borderColor: data.active ? data.color.accent : data.color.border,
        boxShadow: data.active ? `0 0 0 1px ${data.color.accent}` : undefined,
      }}
    >
      {/* The header is the lane's drag handle — `dragHandle` on the rfNode
       * points to `.planspace-lane-drag-handle` so React Flow only starts a
       * lane drag from here. Inner buttons mark themselves `nodrag` to keep
       * click behavior intact. */}
      <div
        className="planspace-lane-drag-handle flex h-8 w-full cursor-grab items-center gap-2 border-b px-3 text-[10px] font-medium uppercase tracking-[0.14em] transition hover:bg-surface-raised/40 active:cursor-grabbing"
        style={{
          borderColor: data.color.border,
          color: data.color.text,
        }}
      >
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            ctx.onSelectPlanspace(data.planspaceId);
          }}
          onMouseDown={(event) => event.stopPropagation()}
          className="nodrag flex min-w-0 flex-1 items-center gap-2 text-left"
          title="Open direction notebook"
        >
          <span
            className="inline-block h-1.5 w-1.5 flex-none rounded-full"
            style={{ background: data.color.accent }}
            aria-hidden="true"
          />
          <span className="truncate">{data.label}</span>
          {data.active && (
            <span className="flex-none rounded border border-current/30 px-1 py-px font-mono text-[9px] opacity-80">
              active
            </span>
          )}
          <span className="flex-none font-mono opacity-70">{data.nodeCount} nodes</span>
        </button>
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            ctx.onTogglePlanspaceVisibility(data.planspaceId, true);
          }}
          onMouseDown={(event) => event.stopPropagation()}
          className="nodrag flex-none rounded px-1 text-[12px] opacity-70 hover:bg-surface-raised hover:opacity-100"
          title="Hide direction"
          aria-label="Hide direction"
        >
          ◌
        </button>
      </div>
    </div>
  );
}

export const PlanspaceLaneNode = memo(PlanspaceLaneNodeImpl);

/* Module-level singleton: App.tsx wires the selection callback so the lane
 * header can route clicks without prop-drilling through React Flow data. */
export type PlanspaceLaneContext = {
  onSelectPlanspace: (planspaceId: string) => void;
  onTogglePlanspaceVisibility: (planspaceId: string, hidden: boolean) => void;
};

let planspaceLaneContext: PlanspaceLaneContext = {
  onSelectPlanspace: () => {},
  onTogglePlanspaceVisibility: () => {},
};

export function setPlanspaceLaneContext(ctx: PlanspaceLaneContext): void {
  planspaceLaneContext = ctx;
}
