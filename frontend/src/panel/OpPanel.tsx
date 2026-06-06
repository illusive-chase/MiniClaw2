import type { NodeDiff, NodeInfo } from "../types";

export function OpPanel({
  node,
  diff,
  diffLoading,
}: {
  node: NodeInfo;
  diff: NodeDiff | null;
  diffLoading: boolean;
}) {
  const opKind = node.op_kind ?? "op";
  const before = node.commit_before ?? null;
  const after = node.commit_after ?? null;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-line bg-surface-raised px-4 py-3">
        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          Repository operation
        </div>
        <h2 className="mt-1 font-display text-[15px] font-semibold leading-snug text-ink-strong">
          {opKind}
        </h2>
        {node.summary && (
          <div className="mt-1 text-[11px] text-ink-muted">{node.summary}</div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto bg-surface px-4 py-3 text-sm">
        <section className="mb-4 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px]">
          <div className="flex items-baseline justify-between text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
            <span>Transition</span>
          </div>
          <div className="mt-1 font-mono text-ink">
            <span className="text-ink-muted">{before ? before.slice(0, 12) : "(none)"}</span>{" "}
            <span className="text-ink-subtle">→</span>{" "}
            <span className="text-ink-strong">{after ? after.slice(0, 12) : "(none)"}</span>
          </div>
        </section>

        <section className="mb-4">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
            Diff
          </div>
          {diffLoading ? (
            <div className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px] text-ink-muted">
              Loading diff…
            </div>
          ) : !diff || !diff.text ? (
            <div className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11.5px] text-ink-muted">
              No file changes.
            </div>
          ) : diff.error ? (
            <pre className="whitespace-pre-wrap rounded-md border border-state-error/30 bg-state-error-soft p-3 text-xs text-state-error">
              {diff.error}
            </pre>
          ) : (
            <pre className="overflow-auto rounded-md border border-line bg-surface-sunken p-3 font-mono text-[11px] leading-relaxed">
              {diff.text.split("\n").map((line, idx) => (
                <div key={idx} className={diffLineClass(line)}>
                  {line || " "}
                </div>
              ))}
            </pre>
          )}
        </section>
      </div>
    </div>
  );
}

function diffLineClass(line: string): string {
  if (line.startsWith("diff --git")) return "text-brand-ink dark:text-brand";
  if (line.startsWith("+++") || line.startsWith("---")) return "text-ink-muted";
  if (line.startsWith("@@")) return "text-brand";
  if (line.startsWith("+")) return "text-state-review dark:text-state-review";
  if (line.startsWith("-")) return "text-state-error";
  return "text-ink-muted";
}
