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
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/80 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-[640px] max-w-[95vw] flex-col rounded-lg border border-slate-800 bg-slate-900 shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-800 px-5 py-3">
          <div>
            <div className="text-sm font-semibold text-slate-100">
              Launch gate node
            </div>
            <div className="text-[11px] text-slate-500">
              The agent runs, then the node waits for your review against this contract.
            </div>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="rounded px-2 py-1 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-200"
          >
            Esc
          </button>
        </div>

        <div className="flex flex-1 flex-col gap-3 overflow-y-auto px-5 py-4 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] font-medium uppercase tracking-wider text-slate-500">
              Prompt
            </span>
            <textarea
              ref={promptRef}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              placeholder="What should the agent do?"
              className="resize-none rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-sm focus:border-slate-600 focus:outline-none"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] font-medium uppercase tracking-wider text-slate-500">
              Contract (markdown)
            </span>
            <textarea
              value={contract}
              onChange={(e) => setContract(e.target.value)}
              rows={14}
              className="resize-none rounded-md border border-slate-800 bg-slate-950 px-3 py-2 font-mono text-xs leading-relaxed focus:border-slate-600 focus:outline-none"
            />
          </label>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-800 px-5 py-3">
          <button
            type="button"
            onClick={onCancel}
            className="rounded border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!canSubmit}
            onClick={() => onLaunch(prompt.trim(), contract)}
            className="rounded bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-900 hover:bg-white disabled:opacity-40"
          >
            Launch gate
          </button>
        </div>
      </div>
    </div>
  );
}
