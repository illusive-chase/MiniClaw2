import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { TemplatePortNodeData } from "../layout";

/** One declared input port of the template being edited in an embedded session.
 *
 * Not an agent: it carries no run state, has no lifecycle actions, and never
 * reaches the scheduler. Its only job is to be the visible upstream end of an
 * `in:<name>` dependency, so the template's signature is something the author
 * can see rather than infer from prompt text.
 *
 * Read-only by design in this round: ports are authored in the template editor,
 * and the session API exposes open/commit/discard but no port mutation. Adding
 * rename/remove here means adding a write endpoint for them first — otherwise
 * the buttons would be unable to persist anything. */
function TemplatePortNodeImpl({
  data,
  selected,
}: NodeProps<TemplatePortNodeData>) {
  const { name, description, consumerIds, unreferenced } = data;
  return (
    <div
      className="group relative w-[168px]"
      title={
        description ||
        (unreferenced
          ? `输入端口 ${name}（未被任何节点引用）`
          : `输入端口 ${name}`)
      }
    >
      <div
        className={
          "relative select-none rounded-full border-2 border-dashed px-3 py-2 text-left shadow-card transition " +
          (selected
            ? "border-brand bg-brand-soft"
            : unreferenced
              ? "border-state-waiting/60 bg-state-waiting-soft hover:border-state-waiting"
              : "border-line-strong bg-surface-raised hover:border-brand/60")
        }
      >
        <div className="truncate font-mono text-[11.5px] text-ink-strong">
          ⟨in:{name}⟩
        </div>
        <div className="mt-0.5 truncate text-[10px] text-ink-subtle">
          {unreferenced ? "未被引用" : `→ ${consumerIds.length} 个节点`}
        </div>
        {/* Source only: a port is an out-of-graph origin, so nothing upstream can
         * connect into it. `isConnectableEnd={false}` also drops React Flow's
         * `connectionindicator` class, which restores `pointer-events: none` and
         * keeps the crosshair cursor from suggesting an inbound drop target. */}
        <Handle
          type="source"
          position={Position.Right}
          isConnectableEnd={false}
          className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0 group-hover:!opacity-100"
        />
      </div>
    </div>
  );
}

export const TemplatePortNode = memo(TemplatePortNodeImpl);
