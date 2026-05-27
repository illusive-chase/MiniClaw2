import type { Activity, ResultKind } from "../types";

export function ToolActivity({ items }: { items: Activity[] }) {
  if (items.length === 0) return null;
  return (
    <div className="space-y-1">
      {items.map((a) => (
        <div
          key={a.id}
          className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs"
        >
          <div className="flex items-start gap-2">
            <StatusDot status={a.status} />
            <div className="min-w-0 flex-1">
              <div className="font-mono text-ink-strong">
                <span className="text-ink-muted">{a.kind}:</span>{" "}
                {a.name || "(unknown)"}
              </div>
              {a.summary && (
                <div className="mt-0.5 truncate font-mono text-[11px] text-ink-muted">
                  {a.summary}
                </div>
              )}
            </div>
          </div>
          {a.result && a.status !== "progress" && (
            <details className="mt-2" open={a.status === "failed"}>
              <summary
                className={
                  "cursor-pointer select-none text-[11px] " +
                  (a.status === "failed"
                    ? "text-state-error"
                    : "text-ink-muted hover:text-ink")
                }
              >
                {a.status === "failed" ? "error output" : "output"} ({a.result.length}{" "}
                chars)
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
      <pre className="mt-2 max-h-96 overflow-auto rounded-md border border-line bg-surface-raised p-2 font-mono text-[11px] leading-snug">
        {text.split("\n").map((line, i) => (
          <div key={i} className={diffLineClass(line)}>
            {line || " "}
          </div>
        ))}
      </pre>
    );
  }
  return (
    <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-surface-raised p-2 font-mono text-[11px] leading-snug text-ink">
      {text}
    </pre>
  );
}

function diffLineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "text-ink-muted";
  if (line.startsWith("@@")) return "text-brand";
  if (line.startsWith("+")) return "text-state-review";
  if (line.startsWith("-")) return "text-state-error";
  return "text-ink-muted";
}

function StatusDot({ status }: { status: Activity["status"] }) {
  const cls = {
    start: "bg-state-waiting pulse-slow",
    progress: "bg-state-waiting pulse-slow",
    finish: "bg-state-review",
    failed: "bg-state-error",
  }[status];
  return (
    <span className={`mt-1.5 inline-block h-2 w-2 rounded-full ${cls}`} />
  );
}
