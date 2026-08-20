import type { NodeProps } from "reactflow";
import type { CommitColumnHeaderData } from "../layout";

export function CommitColumnHeaderNode({ data }: NodeProps<CommitColumnHeaderData>) {
  const { host, head } = data;
  const recorded = host?.recorded_at
    ? new Date(host.recorded_at * 1000).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "未知时间";
  return (
    <div className="w-[164px] rounded-md border border-line bg-surface-raised px-2.5 py-1.5 shadow-sm">
      <div className="truncate text-[10px] font-semibold text-ink-strong">
        {host?.label || host?.mid || "其他设备"}
      </div>
      <div className="mt-0.5 truncate font-mono text-[9px] text-ink-muted">
        HEAD {head.slice(0, 7)} · 快照于 {recorded}
      </div>
      {host?.dirty && (
        <div className="mt-0.5 text-[9px] text-state-waiting">有未提交变更</div>
      )}
    </div>
  );
}
