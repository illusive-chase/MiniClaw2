import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { NodeArtifact, NodeInfo } from "../types";

export type ArtifactPanelProps = {
  /** the agent or gate that produced this file */
  owner: NodeInfo | null;
  artifact: NodeArtifact | null;
  artifactLoading: boolean;
  path: string;
  artifactKind: string;
  /** downstream nodes that loaded this file as context — empty for v1 */
  consumers: NodeInfo[];
  onSelectOwner: (nodeId: string) => void;
  onSelectConsumer: (nodeId: string) => void;
};

const KIND_LABELS: Record<string, string> = {
  summary: "result.md",
  interface: "result.json",
  review_brief: "brief.md",
  review_response: "review.json",
};

export function ArtifactPanel(props: ArtifactPanelProps) {
  const { owner, artifact, artifactLoading, path, artifactKind, consumers, onSelectOwner, onSelectConsumer } =
    props;
  const label = KIND_LABELS[artifactKind] ?? "artifact";
  const filename = filenameOf(path);
  const isJson = artifactKind === "interface" || artifactKind === "review_response";

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-line bg-surface-raised px-4 py-3">
        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          {label}
        </div>
        <h2 className="mt-1 truncate font-display text-[15px] font-semibold leading-snug text-ink-strong">
          {filename}
        </h2>
        <div className="mt-1 truncate font-mono text-[10.5px] text-ink-muted" title={path}>
          {path}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto bg-surface px-4 py-3 text-sm">
        <section className="mb-5">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
            Content
          </div>
          {artifactLoading ? (
            <div className="rounded-md border border-line bg-surface-sunken px-3 py-3 text-[12px] text-ink-muted">
              Loading file…
            </div>
          ) : !artifact ? (
            <div className="rounded-md border border-line bg-surface-sunken px-3 py-3 text-[12px] text-ink-muted">
              No file content available.
            </div>
          ) : artifact.error ? (
            <pre className="whitespace-pre-wrap rounded-md border border-state-error/30 bg-state-error-soft px-3 py-3 text-xs text-state-error">
              {artifact.error}
            </pre>
          ) : !artifact.exists ? (
            <div className="rounded-md border border-line bg-surface-sunken px-3 py-3 text-[12px] text-ink-muted">
              File not written yet.
            </div>
          ) : isJson ? (
            <pre className="whitespace-pre-wrap rounded-md border border-line bg-surface-sunken px-3 py-3 font-mono text-[11px] leading-relaxed text-ink-strong">
              {artifact.content || "{}"}
            </pre>
          ) : (
            <div className="md-prose rounded-md border border-line bg-surface-raised px-4 py-3 shadow-card">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
              >
                {artifact.content || ""}
              </ReactMarkdown>
            </div>
          )}
        </section>

        {owner && (
          <section className="mb-4">
            <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Produced by
            </div>
            <button
              type="button"
              onClick={() => onSelectOwner(owner.id)}
              className="block w-full rounded-md border border-line bg-surface-raised px-3 py-2 text-left text-[12px] transition hover:border-line-strong"
              title="Jump to the producing run"
            >
              <div className="line-clamp-1 text-ink-strong">
                {(owner.summary || owner.prompt || owner.id.slice(0, 8)).trim()}
              </div>
              <div className="mt-0.5 font-mono text-[10.5px] text-ink-muted">
                {owner.kind} · {owner.id.slice(0, 8)}
              </div>
            </button>
          </section>
        )}

        {consumers.length > 0 && (
          <section className="mb-4">
            <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Used by
            </div>
            <ul className="space-y-1">
              {consumers.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => onSelectConsumer(c.id)}
                    className="block w-full rounded-md border border-line bg-surface-raised px-3 py-2 text-left text-[12px] transition hover:border-line-strong"
                  >
                    <div className="line-clamp-1 text-ink-strong">
                      {(c.summary || c.prompt || c.id.slice(0, 8)).trim()}
                    </div>
                    <div className="mt-0.5 font-mono text-[10.5px] text-ink-muted">
                      {c.kind} · {c.id.slice(0, 8)}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}

function filenameOf(p: string): string {
  if (!p) return "(unnamed)";
  const norm = p.replace(/\\/g, "/");
  const idx = norm.lastIndexOf("/");
  return idx >= 0 ? norm.slice(idx + 1) : norm;
}
