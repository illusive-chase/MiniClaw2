import { memo, useState } from "react";
import type { Activity, ResultKind } from "../types";

export const ToolActivity = memo(function ToolActivity({ items }: { items: Activity[] }) {
  if (items.length === 0) return null;
  return (
    <div className="space-y-1">
      {items.map((a) => (
        <ActivityItem key={a.id} activity={a} />
      ))}
    </div>
  );
}, areActivityListsEqual);

function ActivityItem({ activity }: { activity: Activity }) {
  const [parametersExpanded, setParametersExpanded] = useState(false);
  const parameters = activity.parameters || activity.command || activity.summary;
  return (
    <div className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs">
      <div className="flex items-start gap-2">
        <StatusDot status={activity.status} />
        <div className="min-w-0 flex-1">
          <div className="font-mono text-ink-strong">
            <span className="text-ink-muted">{activity.kind}:</span>{" "}
            {activity.name || "(unknown)"}
          </div>
          {parameters && (
            <button
              type="button"
              aria-expanded={parametersExpanded}
              onClick={() => setParametersExpanded((expanded) => !expanded)}
              className={
                "mt-0.5 block w-full cursor-text text-left font-mono text-[11px] leading-relaxed text-ink-muted hover:text-ink " +
                (parametersExpanded
                  ? "max-h-64 overflow-auto whitespace-pre-wrap break-all"
                  : "truncate whitespace-nowrap")
              }
            >
              {parameters}
            </button>
          )}
        </div>
      </div>
      {activity.result && (
        <details
          className="mt-2"
          open={activity.status === "failed" || activity.status === "progress"}
        >
          <summary
            className={
              "cursor-pointer select-none text-[11px] " +
              (activity.status === "failed"
                ? "text-state-error"
                : "text-ink-muted hover:text-ink")
            }
          >
            {activity.status === "failed"
              ? "error output"
              : activity.status === "progress"
                ? "live output"
                : "output"}{" "}
            ({activity.result.length} chars)
          </summary>
          <ResultBlock
            kind={activity.result_kind ?? "text"}
            text={activity.result}
          />
        </details>
      )}
    </div>
  );
}

function areActivityListsEqual(
  previous: { items: Activity[] },
  next: { items: Activity[] },
): boolean {
  if (previous.items === next.items) return true;
  if (previous.items.length !== next.items.length) return false;
  return previous.items.every((activity, index) => {
    const candidate = next.items[index];
    return (
      activity.id === candidate.id &&
      activity.kind === candidate.kind &&
      activity.status === candidate.status &&
      activity.name === candidate.name &&
      activity.summary === candidate.summary &&
      activity.parameters === candidate.parameters &&
      activity.command === candidate.command &&
      activity.result === candidate.result &&
      activity.result_kind === candidate.result_kind
    );
  });
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
