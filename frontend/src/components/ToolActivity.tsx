import type { Activity } from "../types";

export function ToolActivity({ items }: { items: Activity[] }) {
  if (items.length === 0) return null;
  return (
    <div className="space-y-1">
      {items.map((a) => (
        <div
          key={a.id}
          className="flex items-start gap-2 rounded-md border border-slate-800 bg-slate-900/60 px-3 py-2 text-xs"
        >
          <StatusDot status={a.status} />
          <div className="flex-1 min-w-0">
            <div className="font-mono text-slate-300">
              <span className="text-slate-400">{a.kind}:</span> {a.name || "(unknown)"}
            </div>
            {a.summary && (
              <div className="mt-0.5 truncate font-mono text-[11px] text-slate-500">
                {a.summary}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function StatusDot({ status }: { status: Activity["status"] }) {
  const cls = {
    start: "bg-amber-400 animate-pulse",
    progress: "bg-amber-400 animate-pulse",
    finish: "bg-emerald-500",
    failed: "bg-rose-500",
  }[status];
  return <span className={`mt-1.5 inline-block h-2 w-2 rounded-full ${cls}`} />;
}
