import type { ContextBundle, NodeInfo } from "../types";

export type ContextNodePanelProps = {
  identityKey: string;
  path: string;
  /** node ids that loaded this context */
  loadedByNodeIds: string[];
  nodesById: Map<string, NodeInfo>;
  /** the bundle from the most-recent loader, used to read file content */
  sampleBundle: ContextBundle | null;
  onSelectConsumer: (nodeId: string) => void;
};

/**
 * Side-panel view for a context node (a file pulled into a context bundle).
 *
 * Shows the file's plain-language role, char count, and the list of agents
 * that loaded it. The body of the file is rendered when the bundle includes it.
 */
export function ContextNodePanel({
  path,
  loadedByNodeIds,
  nodesById,
  sampleBundle,
  onSelectConsumer,
}: ContextNodePanelProps) {
  const source = sampleBundle?.sources.find((s) => s.path === path) ?? null;
  const description = plainLanguageDescription(source?.scope, source?.kind);
  const filename = filenameOf(path);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-line bg-surface-raised px-4 py-3">
        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          Context file
        </div>
        <h2 className="mt-1 truncate font-display text-[15px] font-semibold leading-snug text-ink-strong">
          {filename}
        </h2>
        <div className="mt-1 truncate font-mono text-[10.5px] text-ink-muted" title={path}>
          {path}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto bg-surface px-4 py-3 text-sm">
        <section className="mb-4">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
            What is this?
          </div>
          <div className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[12.5px] text-ink-strong">
            {description}
          </div>
        </section>

        <section className="mb-4">
          <div className="mb-1 flex items-baseline justify-between text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
            <span>Content</span>
            {source && (
              <span className="font-mono normal-case tracking-normal text-ink-subtle">
                {source.chars} chars
              </span>
            )}
          </div>
          <ContextFileContent path={path} bundle={sampleBundle} />
        </section>

        <section className="mb-4">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
            Loaded by ({loadedByNodeIds.length})
          </div>
          {loadedByNodeIds.length === 0 ? (
            <div className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[12px] text-ink-muted">
              No runs have loaded this file yet.
            </div>
          ) : (
            <ul className="space-y-1">
              {loadedByNodeIds.map((id) => {
                const node = nodesById.get(id);
                if (!node) return null;
                return (
                  <li key={id}>
                    <button
                      type="button"
                      onClick={() => onSelectConsumer(id)}
                      className="block w-full rounded-md border border-line bg-surface-raised px-3 py-2 text-left text-[12px] transition hover:border-line-strong"
                    >
                      <div className="line-clamp-1 text-ink-strong">
                        {(node.summary || node.prompt || node.id.slice(0, 8)).trim()}
                      </div>
                      <div className="mt-0.5 font-mono text-[10.5px] text-ink-muted">
                        {node.kind} · {node.id.slice(0, 8)}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

function ContextFileContent({
  path,
  bundle,
}: {
  path: string;
  bundle: ContextBundle | null;
}) {
  if (!bundle) {
    return (
      <div className="rounded-md border border-line bg-surface-sunken px-3 py-3 text-[12px] text-ink-muted">
        Content not captured for this run.
      </div>
    );
  }
  const systemText = bundle.system_text ?? "";
  const turnText = bundle.turn_text ?? "";
  const guess = extractFromBundleText(systemText, turnText, path);
  if (!guess) {
    return (
      <div className="rounded-md border border-line bg-surface-sunken px-3 py-3 text-[12px] text-ink-muted">
        File contents not embedded in this run's context — read them from disk.
      </div>
    );
  }
  return (
    <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-md border border-line bg-surface-sunken px-3 py-2 font-mono text-[11px] leading-relaxed text-ink">
      {guess}
    </pre>
  );
}

/**
 * Backend embeds context-file content into `bundle.system_text` / `turn_text`
 * with file markers. This is a best-effort lift; precise parsing is the
 * backend's job. We grep for the path and slice between marker lines.
 */
function extractFromBundleText(systemText: string, turnText: string, path: string): string | null {
  const haystack = `${systemText}\n${turnText}`;
  if (!haystack.includes(path)) return null;
  /* Try to lift a single triple-backtick / "----" delimited chunk after the path
   * mention. Best effort. */
  const ix = haystack.indexOf(path);
  if (ix < 0) return null;
  const after = haystack.slice(ix);
  const fenced = after.match(/```[a-z]*\n([\s\S]*?)```/);
  if (fenced) return fenced[1];
  const dashed = after.match(/-----+\n([\s\S]*?)-----+/);
  if (dashed) return dashed[1];
  return null;
}

function plainLanguageDescription(scope?: string, kind?: string): string {
  if (kind === "planspace") {
    return "Project memory — a notebook of plans and decisions the agent reads at the start of every run.";
  }
  if (kind === "memory") {
    return "A memory file — long-lived facts the assistant carries across runs.";
  }
  if (kind === "global") {
    return "A global context file — applied to every project on this machine.";
  }
  if (kind === "binding") {
    return "The wiring that connects this project to its memory profile.";
  }
  if (scope === "system") return "System-level context attached to every run.";
  if (scope === "session") return "Session-level note pulled in for this conversation.";
  if (scope === "project") return "Project file pulled into the agent's working context.";
  return "Context file pulled into the agent's working context.";
}

function filenameOf(p: string): string {
  if (!p) return "(unnamed)";
  const norm = p.replace(/\\/g, "/");
  const idx = norm.lastIndexOf("/");
  return idx >= 0 ? norm.slice(idx + 1) : norm;
}
