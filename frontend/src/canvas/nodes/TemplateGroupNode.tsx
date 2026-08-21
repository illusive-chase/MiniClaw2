import { memo } from "react";
import type { NodeProps } from "reactflow";
import type { TemplateGroupData } from "../layout";
import { TemplateInstanceSummary } from "./TemplateInstanceSummary";

/**
 * The frame around an expanded template instance. It is a lane sibling of its
 * members rather than their React Flow parent, so member drag, extent and lane
 * fitting behave exactly as they do outside a group — the frame is redrawn from
 * the members' bounds on each rebuild.
 *
 * Only the header band takes pointer events (the body is `pointerEvents: none`
 * on the node wrapper), so clicks and marquee selection reach the members
 * underneath instead of hitting the frame.
 */
function TemplateGroupNodeImpl({ data, selected }: NodeProps<TemplateGroupData>) {
  const accent = data.color?.accent;
  const border = data.color?.border ?? "rgb(var(--border))";
  return (
    <div
      /* `overflow-hidden` keeps the header's hover background inside the
       * rounded border, matching the planspace lane. */
      className="overflow-hidden rounded-lg border border-dashed"
      style={{
        width: data.width,
        height: data.height,
        borderColor: selected ? accent ?? border : border,
        background: data.color?.bg ?? "transparent",
        boxShadow: selected && accent ? `0 0 0 1px ${accent}` : undefined,
      }}
    >
      <div
        className="pointer-events-auto flex h-[34px] w-full cursor-pointer items-center gap-2 border-b border-dashed px-2.5 transition hover:bg-surface-raised/40"
        style={{ borderColor: border, color: data.color?.text }}
        title={`模板实例 ${data.label} · 点击选择并折叠`}
        onClick={() => {
          templateGroupContext.onToggleCollapsed(data.instanceId, true);
        }}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <span className="flex-none font-mono text-[11px] leading-none opacity-70">
          ⌄
        </span>
        <TemplateInstanceSummary
          label={data.label}
          argumentSummary={data.argumentSummary}
          progress={data.progress}
        />
        {templateGroupContext.canDelete(data.instanceId) && (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              void templateGroupContext.onDelete(data.instanceId);
            }}
            onMouseDown={(event) => event.stopPropagation()}
            className="nodrag ml-auto inline-flex h-5 w-5 flex-none items-center justify-center rounded border border-state-error/45 bg-surface-raised/80 text-[12px] leading-none text-state-error transition hover:border-state-error hover:bg-state-error-soft"
            title="删除这组 virtual 模板节点"
            aria-label="删除这组 virtual 模板节点"
          >
            ×
          </button>
        )}
      </div>
    </div>
  );
}

export const TemplateGroupNode = memo(TemplateGroupNodeImpl);

export type TemplateGroupContext = {
  onToggleCollapsed: (instanceId: string, collapsed: boolean) => void;
  canDelete: (instanceId: string) => boolean;
  onDelete: (instanceId: string) => Promise<void>;
};

let templateGroupContext: TemplateGroupContext = {
  onToggleCollapsed: () => {},
  canDelete: () => false,
  onDelete: async () => {},
};

export function setTemplateGroupContext(ctx: TemplateGroupContext): void {
  templateGroupContext = ctx;
}
