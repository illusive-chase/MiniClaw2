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
        borderColor: data.focused ? data.color.accent : data.color.border,
        boxShadow: data.focused ? `0 0 0 1px ${data.color.accent}` : undefined,
      }}
    >
      <div
        className={`planspace-lane-drag-handle pointer-events-auto flex h-8 w-full ${data.canMove ? "cursor-grab active:cursor-grabbing" : "cursor-pointer"} items-center gap-2 border-b px-3 text-[10px] font-medium uppercase tracking-[0.14em] transition hover:bg-surface-raised/40`}
        style={{
          borderColor: data.color.border,
          color: data.color.text,
        }}
        title={data.canMove ? "点击打开方向；拖动标题栏移动并保存位置" : "点击打开方向；当前项目只读"}
      >
        <span
          className="inline-block h-1.5 w-1.5 flex-none rounded-full"
          style={{ background: data.color.accent }}
          aria-hidden="true"
        />
        <span className="truncate">{data.label}</span>
        {data.focused && (
          <span className="flex-none rounded border border-current/30 px-1 py-px text-[9px] opacity-80">
            当前
          </span>
        )}
        {/* Auto lanes advance on their own, whether or not anyone is looking
          * at them, so this is a permanent property of the lane rather than
          * a "waiting to be activated" state. */}
        {data.auto && (
          <span
            className="flex-none rounded border px-1 py-px text-[9px] font-medium"
            style={{ borderColor: data.color.accent, color: data.color.accent }}
            title="自动方向：依赖满足后自动开始执行"
          >
            ⟳ 自动
          </span>
        )}
        <span className="ml-auto flex-none font-mono opacity-70">{data.nodeCount} nodes</span>
        {/* Create lands where the user is looking, not where the backend
          * happens to point. This is the whole of Phase 1 in one line. */}
        {data.focused && (
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
};

let planspaceLaneContext: PlanspaceLaneContext = {
  onSelectPlanspace: () => {},
  onTogglePlanspaceVisibility: () => {},
  onCreateVirtual: () => {},
};

export function setPlanspaceLaneContext(ctx: PlanspaceLaneContext): void {
  planspaceLaneContext = ctx;
}
