import { useEffect, useState } from "react";
import type { InteractionRequest, NodeInfo } from "../types";

type Decision = "write-json" | "no-op";

export type GatePanelProps = {
  node: NodeInfo;
  pending: InteractionRequest | null;
  /** the upstream brief artifact, if any — clicking it focuses that node */
  briefArtifact?: { ownerNodeId: string; path: string } | null;
  onSelectBrief: (ownerNodeId: string) => void;
  onSubmit: (payload: {
    id: string;
    decision: Decision;
    path?: string;
    payload?: unknown;
    notes?: string;
  }) => void;
};

/**
 * Side-panel view when a gate is selected.
 *
 * The brief is its own artifact node (§4.3) — click it on the canvas to read.
 * This panel only holds the *response form*: write a JSON file (write-json)
 * or skip (no-op). Notes are optional.
 */
export function GatePanel({
  node,
  pending,
  briefArtifact,
  onSelectBrief,
  onSubmit,
}: GatePanelProps) {
  const [decision, setDecision] = useState<Decision>("write-json");
  const [path, setPath] = useState("out/review.json");
  const [jsonText, setJsonText] = useState(
    '{\n  "approved": true,\n  "notes": ""\n}\n',
  );
  const [notes, setNotes] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);

  useEffect(() => {
    setParseError(null);
  }, [pending?.id]);

  const lastError =
    pending && typeof pending.tool_input?.last_error === "string"
      ? (pending.tool_input.last_error as string)
      : null;

  const handleSubmit = () => {
    if (!pending) return;
    setParseError(null);
    if (decision === "write-json") {
      let parsed: unknown;
      try {
        parsed = JSON.parse(jsonText);
      } catch (err) {
        setParseError(err instanceof Error ? err.message : "invalid JSON");
        return;
      }
      onSubmit({
        id: pending.id,
        decision: "write-json",
        path: path.trim(),
        payload: parsed,
        notes: notes.trim() || undefined,
      });
    } else {
      onSubmit({
        id: pending.id,
        decision: "no-op",
        notes: notes.trim() || undefined,
      });
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-line bg-surface-raised px-4 py-3">
        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          Review gate
        </div>
        <h2 className="mt-1 line-clamp-2 font-display text-[15px] font-semibold leading-snug text-ink-strong">
          {node.summary || "Review checkpoint"}
        </h2>
        {node.review_outcome && (
          <div
            className={
              "mt-1 text-[11px] " +
              (node.review_outcome === "approved"
                ? "text-state-review"
                : "text-state-error")
            }
          >
            Already resolved · {node.review_outcome}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto bg-surface px-4 py-3 text-sm">
        {briefArtifact && (
          <section className="mb-4">
            <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Brief
            </div>
            <button
              type="button"
              onClick={() => onSelectBrief(briefArtifact.ownerNodeId)}
              className="block w-full rounded-md border border-line bg-surface-raised px-3 py-2 text-left text-[12px] transition hover:border-line-strong"
              title="Open the brief in the side panel"
            >
              <div className="font-mono text-[11.5px] text-ink-strong">
                {filenameOf(briefArtifact.path)}
              </div>
              <div className="mt-0.5 text-[10.5px] text-ink-muted">
                Open the brief node on the canvas to read it.
              </div>
            </button>
          </section>
        )}

        {!pending ? (
          <div className="rounded-md border border-line bg-surface-sunken px-3 py-3 text-[12px] text-ink-muted">
            No pending response. The gate may already be resolved or the upstream
            agent did not complete.
          </div>
        ) : (
          <section className="space-y-3">
            <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Your response
            </div>

            {lastError && (
              <div className="rounded-md border border-state-error/30 bg-state-error-soft p-2 text-xs text-state-error">
                Previous attempt failed: {lastError}
              </div>
            )}

            <div className="flex gap-3 text-xs">
              <label className="flex items-center gap-2 text-ink">
                <input
                  type="radio"
                  checked={decision === "write-json"}
                  onChange={() => setDecision("write-json")}
                  className="accent-brand"
                />
                Write a JSON response
              </label>
              <label className="flex items-center gap-2 text-ink">
                <input
                  type="radio"
                  checked={decision === "no-op"}
                  onChange={() => setDecision("no-op")}
                  className="accent-brand"
                />
                Skip (no-op)
              </label>
            </div>

            {decision === "write-json" && (
              <>
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] uppercase tracking-[0.14em] text-ink-subtle">
                    Path (project-relative)
                  </span>
                  <input
                    type="text"
                    value={path}
                    onChange={(e) => setPath(e.target.value)}
                    className="rounded-md border border-line bg-surface-sunken px-2 py-1 text-xs text-ink-strong focus:border-brand focus:outline-none"
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] uppercase tracking-[0.14em] text-ink-subtle">
                    JSON payload
                  </span>
                  <textarea
                    value={jsonText}
                    onChange={(e) => setJsonText(e.target.value)}
                    rows={10}
                    className="resize-none rounded-md border border-line bg-surface-sunken px-2 py-2 font-mono text-xs leading-relaxed text-ink-strong focus:border-brand focus:outline-none"
                  />
                </label>
                {parseError && (
                  <div className="rounded-md border border-state-error/30 bg-state-error-soft p-2 text-xs text-state-error">
                    JSON parse error: {parseError}
                  </div>
                )}
              </>
            )}

            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase tracking-[0.14em] text-ink-subtle">
                Notes (optional)
              </span>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={2}
                className="resize-none rounded-md border border-line bg-surface-sunken px-2 py-1 text-xs text-ink-strong focus:border-brand focus:outline-none"
              />
            </label>

            <div className="flex justify-end">
              <button
                type="button"
                onClick={handleSubmit}
                disabled={decision === "write-json" && !path.trim()}
                className="rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
              >
                Submit
              </button>
            </div>
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
