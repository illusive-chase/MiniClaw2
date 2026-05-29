import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import type { InteractionRequest, NodeInfo } from "../types";

type Decision = "write-json" | "no-op";

export function GateReviewPanel({
  node,
  pending,
  onSubmit,
}: {
  node: NodeInfo;
  pending: InteractionRequest | null;
  onSubmit: (payload: {
    id: string;
    decision: Decision;
    path?: string;
    payload?: unknown;
    notes?: string;
  }) => void;
}) {
  const lastError =
    pending && typeof pending.tool_input.last_error === "string"
      ? (pending.tool_input.last_error as string)
      : null;
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

  const contract = node.contract || "(no brief)";

  return (
    <div className="flex-1 overflow-y-auto bg-surface px-4 py-4 text-sm">
      <section>
        <h3 className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          Brief
        </h3>
        <div className="md-prose rounded-md border border-line bg-surface-sunken p-3 text-ink-strong">
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
            {contract}
          </ReactMarkdown>
        </div>
      </section>

      {!pending && (
        <p className="mt-4 text-xs text-ink-muted">
          No pending review request. The gate may have already resolved or the
          agent did not complete successfully.
        </p>
      )}

      {pending && (
        <section className="mt-5 space-y-3">
          <h3 className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
            Response
          </h3>

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
              write-json
            </label>
            <label className="flex items-center gap-2 text-ink">
              <input
                type="radio"
                checked={decision === "no-op"}
                onChange={() => setDecision("no-op")}
                className="accent-brand"
              />
              no-op
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
                  rows={8}
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
  );
}
