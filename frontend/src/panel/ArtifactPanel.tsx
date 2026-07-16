import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

import { artifactRawUrl, getNodeArtifact } from "../api";
import type { ArtifactFile, ArtifactRef } from "../types";

export type ArtifactPanelProps = {
  sessionId: string;
  nodeId: string;
  artifact: ArtifactRef;
  ext: "md" | "json" | "html";
};

export function ArtifactPanel({ sessionId, nodeId, artifact, ext }: ArtifactPanelProps) {
  const [file, setFile] = useState<ArtifactFile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rawUrl = artifactRawUrl(sessionId, nodeId, artifact.name);

  useEffect(() => {
    let cancelled = false;
    setFile(null);
    setLoading(true);
    setError(null);
    getNodeArtifact(sessionId, nodeId, artifact.name)
      .then((next) => {
        if (!cancelled) setFile(next);
      })
      .catch((reason) => {
        if (!cancelled) {
          setFile(null);
          setError(String(reason));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, nodeId, artifact.name, artifact.sha256]);

  const jsonPreview = useMemo(() => {
    if (ext !== "json" || !file) return "";
    try {
      return JSON.stringify(JSON.parse(file.text), null, 2);
    } catch {
      return file.text;
    }
  }, [ext, file]);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-line bg-surface-raised px-4 py-3">
        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          {ext.toUpperCase()} artifact
        </div>
        <h2 className="mt-1 truncate font-display text-[15px] font-semibold leading-snug text-ink-strong">
          {artifact.name}
        </h2>
        <div className="mt-1 font-mono text-[10.5px] text-ink-muted">
          node {nodeId.slice(0, 8)}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto bg-surface px-4 py-3 text-sm">
        <dl className="mb-4 space-y-1 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11px]">
          <Meta label="Size" value={formatBytes(file?.bytes ?? artifact.bytes)} />
          <Meta label="Modified" value={formatMtime(file?.mtime ?? artifact.mtime)} />
          <Meta label="SHA-256" value={(file?.sha256 ?? artifact.sha256) || "—"} mono />
        </dl>

        {error && (
          <div className="mb-4 rounded-md border border-state-error/30 bg-state-error-soft p-2 text-xs text-state-error">
            {error}
          </div>
        )}

        {ext === "html" ? (
          <section>
            <p className="mb-3 text-[12px] leading-relaxed text-ink-muted">
              HTML artifacts open in a sandboxed window with scripts enabled and an opaque origin.
            </p>
            <button
              type="button"
              onClick={() => window.open(rawUrl, "_blank", "noopener")}
              className="rounded-md bg-brand px-3 py-1.5 text-[12px] font-medium text-white shadow-card transition hover:brightness-[0.95]"
            >
              Open window
            </button>
          </section>
        ) : (
          <section>
            <div className="mb-1 flex items-baseline justify-between">
              <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
                Preview
              </div>
              {loading && <span className="text-[10px] text-ink-subtle">loading...</span>}
            </div>
            {!file ? (
              <div className="rounded-md border border-line bg-surface-sunken px-3 py-3 text-[12px] text-ink-muted">
                {loading ? "Loading artifact..." : "Artifact not loaded."}
              </div>
            ) : ext === "md" ? (
              <div className="md-prose rounded-md border border-line bg-surface-raised px-4 py-3 text-[13px] leading-relaxed text-ink-strong shadow-card">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
                >
                  {file.text || "_Empty file._"}
                </ReactMarkdown>
              </div>
            ) : (
              <div className="md-prose rounded-md border border-line bg-surface-raised px-3 py-2 text-[12px] shadow-card">
                <ReactMarkdown
                  rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
                >
                  {`~~~json\n${jsonPreview}\n~~~`}
                </ReactMarkdown>
              </div>
            )}
            {file?.truncated && (
              <div className="mt-2 rounded-md border border-state-waiting/30 bg-state-waiting-soft px-3 py-2 text-[11px] text-state-waiting">
                Showing the first 512 KiB. {" "}
                <a href={rawUrl} target="_blank" rel="noreferrer" className="underline">
                  Open full raw file
                </a>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

function Meta({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="grid grid-cols-[64px_minmax(0,1fr)] gap-2">
      <dt className="text-ink-subtle">{label}</dt>
      <dd className={`${mono ? "font-mono " : ""}truncate text-ink`} title={value}>
        {value}
      </dd>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${bytes} B`;
}

function formatMtime(mtime: number): string {
  return mtime > 0 ? new Date(mtime * 1000).toLocaleString() : "—";
}
