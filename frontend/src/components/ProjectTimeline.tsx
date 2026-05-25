import type { NodeInfo, NodeState } from "../types";

export function ProjectTimeline({
  nodes,
  selectedNodeId,
  onSelect,
}: {
  nodes: NodeInfo[];
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
}) {
  return (
    <section className="border-b border-slate-800 bg-slate-950/40 px-6 py-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-[11px] font-medium uppercase tracking-wider text-slate-500">
          Project timeline
        </div>
        <div className="text-[11px] text-slate-600">{nodes.length} nodes</div>
      </div>
      {nodes.length === 0 ? (
        <div className="rounded-md border border-dashed border-slate-800 px-3 py-3 text-xs text-slate-600">
          No nodes yet.
        </div>
      ) : (
        <div className="flex gap-2 overflow-x-auto pb-1">
          {nodes.map((node, index) => {
            const isOp = node.kind === "op";
            const kindLabel = isOp && node.op_kind ? `op · ${node.op_kind}` : node.kind;
            const body =
              isOp
                ? node.summary || "(running)"
                : node.summary || node.prompt || "(empty prompt)";
            return (
              <button
                key={node.id}
                type="button"
                onClick={() => onSelect(node.id)}
                className={
                  "group relative grid h-24 flex-none grid-rows-[auto_1fr_auto] rounded-md border px-3 py-2 text-left transition " +
                  (isOp ? "w-32 " : "w-48 ") +
                  (selectedNodeId === node.id
                    ? "border-sky-500/80 bg-sky-950/30"
                    : isOp
                      ? "border-slate-800/60 bg-slate-900/30 hover:border-slate-700"
                      : "border-slate-800 bg-slate-900/50 hover:border-slate-700")
                }
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[11px] text-slate-400">
                    {index + 1}. {kindLabel}
                  </span>
                  <span className={"h-2 w-2 rounded-full " + stateDot(node.state)} />
                </div>
                <div className="mt-1 line-clamp-2 text-xs leading-5 text-slate-200">
                  {body}
                </div>
                <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-slate-500">
                  <span className="font-mono">{node.id.slice(0, 8)}</span>
                  <span>{formatNodeTime(node.started_at ?? node.created_at)}</span>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

function stateDot(state: NodeState): string {
  switch (state) {
    case "queued":
      return "bg-slate-500";
    case "running":
      return "bg-sky-400 animate-pulse";
    case "waiting":
    case "awaiting_review":
      return "bg-emerald-400 animate-pulse";
    case "done":
      return "bg-slate-400";
    case "error":
      return "bg-rose-500";
    case "cancelled":
      return "bg-zinc-600";
    default:
      return "bg-slate-500";
  }
}

function formatNodeTime(value: number): string {
  return new Date(value * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}
