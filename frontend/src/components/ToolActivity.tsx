import type { Activity, ResultKind } from "../types";

export function ToolActivity({ items }: { items: Activity[] }) {
  if (items.length === 0) return null;
  return (
    <div className="space-y-1">
      {items.map((a) => (
        <div
          key={a.id}
          className="rounded-md border border-slate-800 bg-slate-900/60 px-3 py-2 text-xs"
        >
          <div className="flex items-start gap-2">
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
          {a.result && a.status !== "progress" && (
            <details className="mt-2" open={a.status === "failed"}>
              <summary className="cursor-pointer select-none text-[11px] text-slate-500 hover:text-slate-400">
                {a.status === "failed" ? "error output" : "output"}
                {" "}({a.result.length} chars)
              </summary>
              <ResultBlock kind={a.result_kind ?? "text"} text={a.result} />
            </details>
          )}
        </div>
      ))}
    </div>
  );
}

function ResultBlock({ kind, text }: { kind: ResultKind; text: string }) {
  if (kind === "diff") {
    return (
      <pre className="mt-2 max-h-96 overflow-auto rounded bg-slate-950/60 p-2 font-mono text-[11px] leading-snug">
        {text.split("\n").map((line, i) => (
          <div key={i} className={diffLineClass(line)}>
            {line || " "}
          </div>
        ))}
      </pre>
    );
  }
  return (
    <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-slate-950/60 p-2 font-mono text-[11px] leading-snug text-slate-300">
      {text}
    </pre>
  );
}

function diffLineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "text-slate-400";
  if (line.startsWith("@@")) return "text-cyan-400";
  if (line.startsWith("+")) return "text-emerald-400";
  if (line.startsWith("-")) return "text-rose-400";
  return "text-slate-400";
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
