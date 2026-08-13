import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import type { EditorNode } from "../../templateEditor";
import { nodeHeadline } from "../../templateEditor";

/** A template body node. Deliberately mirrors AgentNode's geometry and chrome
 * so the editor canvas reads as the same surface as the project canvas, minus
 * everything state-driven: a template node has no run state, no usage, and no
 * lifecycle actions. */
export type TemplateNodeData = {
  node: EditorNode;
  /** Argument names this node's prompt references, in scan order. */
  argumentNames: string[];
  /** Input ports this node consumes, via dep or placeholder. */
  inputPorts: string[];
  /** true while this node is the pending source of a connect gesture. */
  linking: boolean;
  onStartLink: (nodeId: string) => void;
  onRemove: (nodeId: string) => void;
};

function TemplateNodeImpl({ data, selected }: NodeProps<TemplateNodeData>) {
  const { node, argumentNames, inputPorts, linking, onStartLink, onRemove } = data;
  const isReview = node.category === "review";
  return (
    <div className="group relative w-[224px]" title={node.prompt || node.id}>
      <div
        className={
          "relative select-none overflow-hidden rounded-lg border bg-surface-raised text-left shadow-card transition " +
          (selected
            ? "border-brand ring-2 ring-brand ring-offset-2 ring-offset-surface-sunken"
            : linking
              ? "border-brand ring-2 ring-brand/30 ring-offset-2 ring-offset-surface-sunken"
              : "border-line hover:border-line-strong hover:ring-2 hover:ring-line-strong/45 hover:ring-offset-2 hover:ring-offset-surface-sunken hover:shadow-raised")
        }
      >
        <span
          className={
            "pointer-events-none absolute inset-y-0 left-0 w-[3px] " +
            (isReview ? "bg-state-review" : "bg-line-strong")
          }
          aria-hidden="true"
        />

        <div className="flex items-center justify-between gap-2 pl-3.5 pr-2.5 pt-2">
          <span
            className={
              "inline-flex items-center rounded border px-1 py-0.5 text-[9px] font-medium uppercase tracking-[0.12em] " +
              (node.category === "planning"
                ? "border-brand/30 bg-brand-soft text-brand-ink"
                : isReview
                  ? "border-state-review/30 bg-state-review-soft text-state-review"
                  : "border-line bg-surface text-ink-muted")
            }
            title={node.subtype ?? node.category}
          >
            {isReview ? (node.subtype === "code_review" ? "code" : "review") : node.category === "planning" ? "plan" : "work"}
          </span>
          <span className="font-mono text-[10px] text-ink-subtle">{node.id}</span>
        </div>

        <div className="line-clamp-3 px-3.5 pt-1.5 text-[12.5px] leading-[1.38] text-ink-strong">
          {nodeHeadline(node)}
        </div>

        <div className="flex flex-wrap items-center gap-1 px-3.5 pb-2 pt-2">
          {argumentNames.map((name) => (
            <span
              key={`arg-${name}`}
              className="rounded border border-brand/35 bg-brand-soft px-1 py-0.5 font-mono text-[9px] text-brand-ink"
              title={`引用参数 {{${name}}}`}
            >
              {`{{${name}}}`}
            </span>
          ))}
          {inputPorts.map((port) => (
            <span
              key={`port-${port}`}
              className="rounded border border-line-strong bg-surface px-1 py-0.5 font-mono text-[9px] text-ink-muted"
              title={`使用输入端口 ${port}`}
            >
              {`in:${port}`}
            </span>
          ))}
          {node.resume_from && (
            <span
              className="rounded border border-brand/40 px-1 py-0.5 font-mono text-[9px] text-brand-ink"
              title={`从 ${node.resume_from} 恢复会话`}
            >
              ↻ {node.resume_from}
            </span>
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

      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onStartLink(node.id);
        }}
        onMouseDown={(event) => event.stopPropagation()}
        className="nodrag absolute -right-3 top-[calc(50%-15px)] z-20 inline-flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-full border border-line-strong bg-surface-raised text-[12px] leading-none text-ink-muted opacity-0 shadow-card transition hover:border-brand/55 hover:bg-brand-soft hover:text-brand group-hover:opacity-100"
        title="从这个节点连一条依赖到另一个节点"
        aria-label={`从 ${node.id} 开始连线`}
      >
        ↘
      </button>
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onRemove(node.id);
        }}
        onMouseDown={(event) => event.stopPropagation()}
        className="nodrag absolute -right-3 top-[calc(50%+15px)] z-20 inline-flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-full border border-state-error/50 bg-surface-raised text-[12px] leading-none text-state-error opacity-0 shadow-card transition hover:border-state-error hover:bg-state-error-soft group-hover:opacity-100"
        title="删除这个节点"
        aria-label={`删除节点 ${node.id}`}
      >
        ×
      </button>
    </div>
  );
}

export const TemplateNode = memo(TemplateNodeImpl);

/** An input-port placeholder: a pure frontend node with no runtime identity.
 *
 * It is not an agent, carries no state, and never reaches the scheduler. Its
 * only job is to be the visible end of an `in:<name>` dependency, so the
 * template's signature is something the author can see and connect. */
export type TemplatePortNodeData = {
  name: string;
  description: string;
  /** Node ids consuming this port; empty renders as the unreferenced warning. */
  consumerIds: string[];
  unreferenced: boolean;
  linking: boolean;
  onStartLink: (port: string) => void;
  onRemove: (port: string) => void;
};

function TemplatePortNodeImpl({ data, selected }: NodeProps<TemplatePortNodeData>) {
  const { name, description, consumerIds, unreferenced, linking, onStartLink, onRemove } =
    data;
  return (
    <div className="group relative w-[168px]" title={description || `输入端口 ${name}`}>
      <div
        className={
          "relative select-none rounded-full border-2 border-dashed px-3 py-2 text-left shadow-card transition " +
          (selected || linking
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
          {unreferenced
            ? "未被引用"
            : consumerIds.length === 1
              ? `→ ${consumerIds[0]}`
              : `→ ${consumerIds.length} 个节点`}
        </div>
        <Handle
          type="source"
          position={Position.Right}
          className="!h-3 !w-3 !border-2 !border-line !bg-surface !opacity-0"
        />
      </div>

      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onStartLink(name);
        }}
        onMouseDown={(event) => event.stopPropagation()}
        className="nodrag absolute -right-2.5 top-[calc(50%-13px)] z-20 inline-flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded-full border border-line-strong bg-surface-raised text-[11px] leading-none text-ink-muted opacity-0 shadow-card transition hover:border-brand/55 hover:bg-brand-soft hover:text-brand group-hover:opacity-100"
        title="把这个端口连到一个节点"
        aria-label={`从端口 ${name} 开始连线`}
      >
        ↘
      </button>
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onRemove(name);
        }}
        onMouseDown={(event) => event.stopPropagation()}
        className="nodrag absolute -right-2.5 top-[calc(50%+13px)] z-20 inline-flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded-full border border-state-error/50 bg-surface-raised text-[11px] leading-none text-state-error opacity-0 shadow-card transition hover:border-state-error hover:bg-state-error-soft group-hover:opacity-100"
        title="删除这个输入端口"
        aria-label={`删除端口 ${name}`}
      >
        ×
      </button>
    </div>
  );
}

export const TemplatePortNode = memo(TemplatePortNodeImpl);
