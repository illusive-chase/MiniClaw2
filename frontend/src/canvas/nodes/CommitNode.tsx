import { Handle, Position, type NodeProps } from "reactflow";
import type { CommitNodeData } from "../layout";
import { useNodeInHoverGroup } from "../hoverStore";

export function CommitNode({ data, selected }: NodeProps<CommitNodeData>) {
  const { commit, head, ghost } = data;
  const nodeId = ghost ? "commit:ghost" : `commit:${commit.sha}`;
  const hoveredByGroup = useNodeInHoverGroup(nodeId);
  const availability = commit.availability ?? (commit.live ? "live" : "stale");
  const border = ghost
    ? "border-dashed border-line-strong"
    : availability === "live"
      ? "border-line-strong"
      : availability === "peer"
        ? "border-dashed border-line-strong text-ink-muted"
        : availability === "unfetched"
          ? "border-dashed border-line opacity-55"
          : availability === "stale"
            ? "border-dashed border-state-waiting"
            : "border-line";
  return (
    <div
      className={`relative flex h-16 w-16 flex-col items-center justify-center rounded-full border-2 bg-surface-raised font-mono shadow-sm transition hover:ring-2 hover:ring-line-strong/45 hover:ring-offset-2 hover:ring-offset-surface-sunken ${border} ${selected ? "ring-2 ring-brand ring-offset-2 ring-offset-surface-sunken" : hoveredByGroup ? "ring-2 ring-line-strong/45 ring-offset-2 ring-offset-surface-sunken" : head ? "ring-2 ring-brand/50" : ""}`}
      title={`${commit.message}${commit.ts ? ` · ${new Date(commit.ts * 1000).toLocaleString()}` : ""}`.trim()}
    >
      <Handle type="target" position={Position.Top} className="!h-1.5 !w-1.5 !border-0 !bg-line-strong" />
      {!ghost && (data.externalCountBefore ?? 0) > 0 && (
        <span className="pointer-events-none absolute -right-2 -top-1 rounded-full border border-line bg-surface-raised px-1.5 py-0.5 text-[9px] text-ink-muted shadow-sm">
          +{data.externalCountBefore}
        </span>
      )}
      <span className="text-[10px] font-semibold text-ink-strong">{ghost ? `+${data.dirtyCount ?? ""}` : commit.sha.slice(0, 7)}</span>
      <span className="max-w-[50px] truncate text-[8px] text-ink-subtle">{ghost ? "changes" : head ? "HEAD" : availability === "peer" ? "peer" : availability === "unfetched" ? "未获取" : "commit"}</span>
      <Handle type="source" position={Position.Bottom} className="!h-1.5 !w-1.5 !border-0 !bg-line-strong" />
    </div>
  );
}
