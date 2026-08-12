import { memo } from "react";
import type { NodeProps } from "reactflow";
import type { PlanspaceLaneData } from "../layout";

function PlanspaceLaneNodeImpl({ data }: NodeProps<PlanspaceLaneData>) {
  return (
    <div
      /* `overflow-hidden` keeps the header's hover background within the rounded
       * border. Without it, the header's `hover:bg-...` paints past the rounded
       * top corners of the lane outline. */
      className="overflow-hidden rounded-md border"
      style={{
        width: data.width,
        height: data.height,
        background: data.color.bg,
        borderColor: data.active ? data.color.accent : data.color.border,
        boxShadow: data.active ? `0 0 0 1px ${data.color.accent}` : undefined,
      }}
    >
      {/* The whole header is the drag handle (no inner buttons that would
       * swallow drag-starts on the label text). Clicking the lane is handled
       * by React Flow's onNodeClick in Canvas, which selects the planspace,
       * so this div doesn't need its own onClick. */}
      <div
        className="planspace-lane-drag-handle pointer-events-auto flex h-8 w-full cursor-grab items-center gap-2 border-b px-3 text-[10px] font-medium uppercase tracking-[0.14em] transition hover:bg-surface-raised/40 active:cursor-grabbing"
        style={{
          borderColor: data.color.border,
          color: data.color.text,
        }}
        title="Drag to move · click to open direction"
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
        {!data.active && data.auto && (
          <span className="flex-none rounded border border-current/30 px-1 py-px text-[9px] opacity-80">
            待激活
          </span>
        )}
        <span className="ml-auto flex-none font-mono opacity-70">{data.nodeCount} nodes</span>
        {!data.active && data.canActivate && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              planspaceLaneContext.onActivatePlanspace(data.planspaceId);
            }}
            onMouseDown={(e) => e.stopPropagation()}
            className="nodrag -mr-1 inline-flex h-5 flex-none items-center justify-center rounded border border-current/30 bg-surface-raised/70 px-1.5 text-[9px] opacity-90 transition hover:bg-surface-raised"
            title="激活此方向"
          >
            激活
          </button>
        )}
        {data.active && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              planspaceLaneContext.onCreateVirtual(data.planspaceId);
            }}
            onMouseDown={(e) => e.stopPropagation()}
            disabled={!data.canCreateVirtual}
            className="nodrag -mr-1 inline-flex h-5 w-5 flex-none items-center justify-center rounded border border-current/30 bg-surface-raised/70 text-[14px] leading-none opacity-90 transition hover:bg-surface-raised disabled:cursor-not-allowed disabled:opacity-35"
            title="Add virtual node"
            aria-label="Add virtual node"
          >
            +
          </button>
        )}
      </div>
    </div>
  );
}

export const PlanspaceLaneNode = memo(PlanspaceLaneNodeImpl);

export type PlanspaceLaneContext = {
  onSelectPlanspace: (planspaceId: string) => void;
  onTogglePlanspaceVisibility: (planspaceId: string, hidden: boolean) => void;
  onCreateVirtual: (planspaceId: string) => void;
  onActivatePlanspace: (planspaceId: string) => void;
};

let planspaceLaneContext: PlanspaceLaneContext = {
  onSelectPlanspace: () => {},
  onTogglePlanspaceVisibility: () => {},
  onCreateVirtual: () => {},
  onActivatePlanspace: () => {},
};

export function setPlanspaceLaneContext(ctx: PlanspaceLaneContext): void {
  planspaceLaneContext = ctx;
}
