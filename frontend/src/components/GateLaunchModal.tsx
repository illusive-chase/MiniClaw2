import { useEffect, useRef, useState } from "react";

const TEMPLATE = `# Expected
What the agent should produce, and where (paths, file types).

# Unexpected
Failure modes, common pitfalls, things to watch for.

# Response protocol
Reviewer writes JSON to: out/review.json
Schema: { approved: boolean, notes: string }
`;

export function GateLaunchModal({
  open,
  onCancel,
  onLaunch,
}: {
  open: boolean;
  onCancel: () => void;
  onLaunch: (prompt: string, contract: string) => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [contract, setContract] = useState(TEMPLATE);
  const promptRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (open) {
      setPrompt("");
      setContract(TEMPLATE);
      window.setTimeout(() => promptRef.current?.focus(), 0);
    }
  }, [open]);

  if (!open) return null;

  const canSubmit = prompt.trim().length > 0 && contract.trim().length > 0;

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-surface-scrim/60 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-[640px] max-w-[95vw] flex-col rounded-xl border border-line bg-surface-raised shadow-modal">
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            <div className="font-display text-sm font-semibold text-ink-strong">
              Launch gate node
            </div>
            <div className="text-[11px] text-ink-muted">
              The agent runs, then the node waits for your review against this contract.
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

        <div className="flex flex-1 flex-col gap-3 overflow-y-auto px-5 py-4 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Prompt
            </span>
            <textarea
              ref={promptRef}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              placeholder="What should the agent do?"
              className="resize-none rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Contract (markdown)
            </span>
            <textarea
              value={contract}
              onChange={(e) => setContract(e.target.value)}
              rows={14}
              className="resize-none rounded-md border border-line bg-surface-sunken px-3 py-2 font-mono text-xs leading-relaxed text-ink-strong focus:border-brand focus:outline-none"
            />
          </label>
        </div>

        <div className="flex items-center justify-end gap-2 rounded-b-xl border-t border-line bg-surface-sunken px-5 py-3">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-line bg-surface px-3 py-1.5 text-xs text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!canSubmit}
            onClick={() => onLaunch(prompt.trim(), contract)}
            className="rounded-md border border-state-review/40 bg-state-review-soft px-3 py-1.5 text-xs font-medium text-state-review shadow-card transition hover:border-state-review/70 disabled:opacity-40"
          >
            Launch gate
          </button>
        </div>
      </div>
    </div>
  );
}
