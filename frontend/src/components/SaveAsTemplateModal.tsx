import { useEffect, useRef, useState } from "react";
import { saveUserTemplate } from "../api";

type Props = {
  open: boolean;
  sessionId: string | null;
  nodeIds: string[];
  onCancel: () => void;
  onSaved: (slug: string) => void;
};

export function SaveAsTemplateModal({
  open,
  sessionId,
  nodeIds,
  onCancel,
  onSaved,
}: Props) {
  const [name, setName] = useState("");
  const [brief, setBrief] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (open) {
      setName("");
      setBrief("");
      setSubmitting(false);
      setError(null);
      window.setTimeout(() => nameRef.current?.focus(), 0);
    }
  }, [open]);

  if (!open) return null;

  const submit = async () => {
    if (!sessionId || !name.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await saveUserTemplate(sessionId, {
        name: name.trim(),
        brief: brief.trim(),
        node_ids: nodeIds,
      });
      onSaved(res.slug);
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
      setSubmitting(false);
    }
  };

  const nodeCount = nodeIds.length;

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-surface-scrim/60 backdrop-blur-sm">
      <div className="flex w-[460px] max-w-[95vw] flex-col rounded-xl border border-line bg-surface-raised shadow-modal">
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            <div className="font-display text-sm font-semibold text-ink-strong">
              Save as template
            </div>
            <div className="text-[11px] text-ink-muted">
              {nodeCount === 1
                ? "1 node will be saved into your template library."
                : `${nodeCount} nodes will be saved into your template library.`}
            </div>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="rounded px-2 py-1 text-[11px] font-medium text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
          >
            Esc
          </button>
        </div>

        <div className="flex flex-col gap-4 px-5 py-4 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Name
            </span>
            <input
              ref={nameRef}
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && name.trim() && !submitting) submit();
              }}
              placeholder="e.g. Draft then critique"
              className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Brief <span className="text-ink-subtle/70">(optional)</span>
            </span>
            <input
              type="text"
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              placeholder="One-line description shown in the library dock"
              className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
            />
          </label>

          {error && (
            <div className="rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-xs text-state-error">
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 rounded-b-xl border-t border-line bg-surface-sunken px-5 py-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="rounded-md border border-line bg-surface px-3 py-1.5 text-xs text-ink-muted transition hover:bg-surface-sunken hover:text-ink disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={submitting || !name.trim() || nodeCount === 0}
            onClick={() => void submit()}
            className="rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
          >
            {submitting ? "Saving…" : "Save template"}
          </button>
        </div>
      </div>
    </div>
  );
}
