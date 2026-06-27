import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

import { getSessionFile } from "../api";
import type { NodeInfo, SessionFile } from "../types";

export type PlanspaceFilePanelProps = {
  sessionId: string;
  loadedByNodeIds: string[];
  nodesById: Map<string, NodeInfo>;
  onSelectConsumer: (nodeId: string) => void;
  reloadVersion?: number;
};

export function PlanspaceFilePanel({
  sessionId,
  loadedByNodeIds,
  nodesById,
  onSelectConsumer,
  reloadVersion = 0,
}: PlanspaceFilePanelProps) {
  const [file, setFile] = useState<SessionFile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getSessionFile(sessionId, "context")
      .then((next) => {
        if (!cancelled) setFile(next);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(String(err));
          setFile(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, reloadVersion]);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-line bg-surface-raised px-4 py-3">
        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          Project notes
        </div>
        <h2 className="mt-1 truncate font-display text-[15px] font-semibold leading-snug text-ink-strong">
          CONTEXT.md
        </h2>
        <div className="mt-1 truncate font-mono text-[10.5px] text-ink-muted">
          {file?.path ?? "project-root"}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto bg-surface px-4 py-3 text-sm">
        {file && (
          <div className="mb-3 font-mono text-[10.5px] text-ink-subtle">
            Last updated {new Date(file.mtime * 1000).toLocaleString()}
            {" · "}
            {writerLabel(file)}
          </div>
        )}

        {error && (
          <div className="mb-4 rounded-md border border-state-error/30 bg-state-error-soft p-2 text-xs text-state-error">
            {error}
          </div>
        )}

        <section className="mb-4">
          <div className="mb-1 flex items-baseline justify-between">
            <SectionLabel>Preview</SectionLabel>
            {loading && <span className="text-[10px] text-ink-subtle">loading...</span>}
          </div>
          {!file ? (
            <div className="rounded-md border border-line bg-surface-sunken px-3 py-3 text-[12px] text-ink-muted">
              {loading ? "Loading file..." : "File not loaded."}
            </div>
          ) : (
            <div className="md-prose rounded-md border border-line bg-surface-raised px-4 py-3 text-[13px] leading-relaxed text-ink-strong shadow-card">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
              >
                {file.text || "_Empty file._"}
              </ReactMarkdown>
            </div>
          )}
        </section>

        <section className="mb-4">
          <SectionLabel>Loaded by ({loadedByNodeIds.length})</SectionLabel>
          {loadedByNodeIds.length === 0 ? (
            <div className="mt-1 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[12px] text-ink-muted">
              No runs have loaded this file yet.
            </div>
          ) : (
            <ul className="mt-1 space-y-1">
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

function writerLabel(file: SessionFile): string {
  const writer = file.last_writer ?? { kind: "hand" };
  if (writer.kind === "node" && writer.node_id) {
    return `updated by node ${writer.node_id.slice(0, 8)}`;
  }
  if (writer.kind === "context-refresh") {
    return `${writer.source === "init" ? "initialized" : "refreshed"} via project menu`;
  }
  return "hand-edited";
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
      {children}
    </div>
  );
}
