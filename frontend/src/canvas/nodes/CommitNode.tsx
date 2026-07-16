import { Handle, Position, type NodeProps } from "reactflow";
import type { CommitNodeData } from "../layout";

export function CommitNode({ data, selected }: NodeProps<CommitNodeData>) {
  const { commit, head, ghost } = data;
  const border = ghost
    ? "border-dashed border-line-strong"
    : commit.live
      ? "border-line-strong"
      : "border-dashed border-state-waiting";
  return (
    <div
      className={`flex h-16 w-16 flex-col items-center justify-center rounded-full border-2 bg-surface-raised font-mono shadow-sm ${border} ${selected ? "ring-2 ring-brand ring-offset-2 ring-offset-surface-sunken" : head ? "ring-2 ring-brand/50" : ""}`}
      title={`${commit.message}${commit.ts ? ` · ${new Date(commit.ts * 1000).toLocaleString()}` : ""}`}
    >
      <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-line-strong" />
      <span className="text-[10px] font-semibold text-ink-strong">{ghost ? `+${data.dirtyCount ?? ""}` : commit.sha.slice(0, 7)}</span>
      <span className="max-w-[50px] truncate text-[8px] text-ink-subtle">{ghost ? "changes" : head ? "HEAD" : "commit"}</span>
      <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-line-strong" />
    </div>
  );
}
