import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { TemplateInstanceBoxData } from "../layout";
import { progressLabel, TemplateInstanceSummary } from "./TemplateInstanceSummary";

/**
 * A whole template instance collapsed into one tile. Its handles are the same
 * left/right pair an agent tile uses, so the dependency edges redirected onto
 * this box by `buildGraph` anchor to its edge: inbound edges are the instance's
 * input bindings, outbound edges are downstream consumers of its sinks.
 */
function TemplateInstanceBoxNodeImpl({
  data,
  selected,
}: NodeProps<TemplateInstanceBoxData>) {
  const accent = data.color?.accent;
  const tone = data.progress.hasError
    ? {
        border: "border-state-error/60",
        bg: "bg-state-error-soft/35",
        text: "text-state-error",
      }
    : data.progress.running > 0
      ? {
          border: "border-state-running/50",
          bg: "bg-state-running-soft/30",
          text: "text-brand-ink dark:text-brand",
        }
      : {
          border: "border-line-strong",
          bg: "bg-surface-raised",
          text: "text-ink-muted",
        };

  return (
    <div
      title={`模板实例 ${data.label} · ${data.memberNodeIds.length} 个节点 · 点击展开`}
      className={
        "relative flex h-[96px] w-[224px] select-none flex-col gap-1.5 rounded-lg border-2 px-2.5 py-2 shadow-card transition " +
        tone.border +
        " " +
        tone.bg +
        (selected
          ? " ring-2 ring-brand ring-offset-2 ring-offset-surface-sunken"
          : " hover:ring-2 hover:ring-line-strong/45 hover:ring-offset-2 hover:ring-offset-surface-sunken")
      }
      style={selected && accent ? { borderColor: accent } : undefined}
    >
      <div className="flex items-center gap-1.5">
        <span className="flex-none font-mono text-[11px] leading-none text-ink-subtle">
          ›
        </span>
        <span className="flex-none rounded border border-line px-1 py-px font-mono text-[8px] uppercase tracking-[0.1em] text-ink-subtle">
          template
        </span>
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            templateInstanceBoxContext.onToggleCollapsed(data.instanceId, false);
          }}
          onMouseDown={(event) => event.stopPropagation()}
          className="nodrag ml-auto inline-flex h-4 flex-none items-center justify-center rounded border border-line bg-surface/70 px-1 font-mono text-[9px] leading-none text-ink-muted transition hover:bg-surface"
          title="展开实例"
          aria-label="展开实例"
        >
          ⌃
        </button>
      </div>
      <TemplateInstanceSummary
        label={data.label}
        argumentSummary={data.argumentSummary}
        progress={data.progress}
        stacked
      />
      <div className="mt-auto flex items-center gap-1.5">
        <span className={"font-mono text-[9px] leading-none " + tone.text}>
          {progressLabel(data.progress)}
        </span>
        <span className="ml-auto font-mono text-[9px] leading-none text-ink-subtle">
          {data.memberNodeIds.length} nodes
        </span>
        {data.canCreateVirtual && data.sinkNodeIds.length > 0 && (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              templateInstanceBoxContext.onCreateDownstream(data.sinkNodeIds);
            }}
            onMouseDown={(event) => event.stopPropagation()}
            className="nodrag inline-flex h-4 w-4 flex-none items-center justify-center rounded border border-line bg-surface/70 text-[12px] leading-none text-ink-muted transition hover:bg-surface"
            title={`新建下游节点 · 依赖实例的 ${data.sinkNodeIds.length} 个输出`}
            aria-label="新建下游节点"
          >
            ↘
          </button>
        )}
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

export const TemplateInstanceBoxNode = memo(TemplateInstanceBoxNodeImpl);

export type TemplateInstanceBoxContext = {
  onToggleCollapsed: (instanceId: string, collapsed: boolean) => void;
  /** Creates a virtual depending on every sink of the instance (§4.3). */
  onCreateDownstream: (sinkNodeIds: string[]) => void;
};

let templateInstanceBoxContext: TemplateInstanceBoxContext = {
  onToggleCollapsed: () => {},
  onCreateDownstream: () => {},
};

export function setTemplateInstanceBoxContext(
  ctx: TemplateInstanceBoxContext,
): void {
  templateInstanceBoxContext = ctx;
}
