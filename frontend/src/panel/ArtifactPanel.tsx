import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

import { artifactRawUrl, getNodeArtifact } from "../api";
import { writeClipboard } from "../clipboard";
import { ZoomableText } from "../components/TextZoom";
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
  const [copyState, setCopyState] = useState<"idle" | "copying" | "copied" | "error">(
    "idle",
  );
  const rawUrl = artifactRawUrl(sessionId, nodeId, artifact.name);

  useEffect(() => {
    let cancelled = false;
    setFile(null);
    setLoading(true);
    setError(null);
    setCopyState("idle");
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

  useEffect(() => {
    if (copyState !== "copied" && copyState !== "error") return;
    const timeout = window.setTimeout(() => setCopyState("idle"), 2000);
    return () => window.clearTimeout(timeout);
  }, [copyState]);

  const copyMarkdown = async () => {
    if (ext !== "md" || !file || copyState === "copying") return;
    setCopyState("copying");
    try {
      let markdown = file.text;
      if (file.truncated) {
        const response = await fetch(rawUrl);
        if (!response.ok) {
          throw new Error(`Raw artifact request failed: ${response.status}`);
        }
        markdown = await response.text();
      }
      await writeClipboard(markdown);
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }
  };

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
            <div className="mb-1 flex min-h-7 items-center justify-between gap-3">
              <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
                Preview
              </div>
              <div className="flex items-center gap-2">
                {loading && <span className="text-[10px] text-ink-subtle">loading...</span>}
                {ext === "md" && (
                  <button
                    type="button"
                    onClick={() => void copyMarkdown()}
                    disabled={!file || copyState === "copying"}
                    className={`inline-flex h-7 items-center gap-1.5 rounded-sm border px-2 text-[11px] font-medium transition disabled:cursor-not-allowed disabled:opacity-45 ${
                      copyState === "error"
                        ? "border-state-error/40 bg-state-error-soft text-state-error"
                        : copyState === "copied"
                          ? "border-state-done/40 bg-state-done-soft text-state-done"
                          : "border-line bg-surface-raised text-ink-muted hover:border-line-strong hover:bg-surface-sunken hover:text-ink-strong"
                    }`}
                    title="Copy raw Markdown"
                    aria-label="Copy raw Markdown"
                  >
                    {copyState === "copied" ? <CopyCheckIcon /> : <CopyIcon />}
                    <span aria-live="polite">
                      {copyState === "copying"
                        ? "Copying..."
                        : copyState === "copied"
                          ? "Copied"
                          : copyState === "error"
                            ? "Copy failed"
                            : "Copy raw"}
                    </span>
                  </button>
                )}
              </div>
            </div>
            {!file ? (
              <div className="rounded-md border border-line bg-surface-sunken px-3 py-3 text-[12px] text-ink-muted">
                {loading ? "Loading artifact..." : "Artifact not loaded."}
              </div>
            ) : ext === "md" ? (
              <ZoomableText
                title={artifact.name}
                subtitle={`node ${nodeId.slice(0, 8)}`}
                text={file.text}
                defaultView="markdown"
                className="rounded-md border border-line bg-surface-raised shadow-card"
              >
                <div className="md-prose px-4 py-3 text-[13px] leading-relaxed text-ink-strong">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
                  >
                    {file.text || "_Empty file._"}
                  </ReactMarkdown>
                </div>
              </ZoomableText>
            ) : (
              <ZoomableText
                title={artifact.name}
                subtitle={`node ${nodeId.slice(0, 8)}`}
                text={jsonPreview}
                rawOnly
                className="rounded-md border border-line bg-surface-raised shadow-card"
              >
                <div className="md-prose px-3 py-2 text-[12px]">
                  <ReactMarkdown
                    rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
                  >
                    {`~~~json\n${jsonPreview}\n~~~`}
                  </ReactMarkdown>
                </div>
              </ZoomableText>
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

function CopyIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="5.25" y="5.25" width="7.5" height="7.5" rx="1" />
      <path d="M10.75 5.25v-2a1 1 0 0 0-1-1h-6.5a1 1 0 0 0-1 1v6.5a1 1 0 0 0 1 1h2" />
    </svg>
  );
}

function CopyCheckIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m3 8.25 3.1 3.1L13 4.75" />
    </svg>
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
