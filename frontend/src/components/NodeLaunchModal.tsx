import { useEffect, useMemo, useRef, useState } from "react";
import type { NodeInfo } from "../types";

type OutputKind = "freeform" | "summary" | "interface" | "review_brief";

type Props = {
  open: boolean;
  onCancel: () => void;
  onLaunch: (
    prompt: string,
    resumeFromNodeId: string | null,
    outputKind: OutputKind,
  ) => void;
  resumeOptions: NodeInfo[];
  presetResumeFromNodeId?: string | null;
  presetOutputKind?: OutputKind;
};

export function NodeLaunchModal({
  open,
  onCancel,
  onLaunch,
  resumeOptions,
  presetResumeFromNodeId,
  presetOutputKind = "summary",
}: Props) {
  const [prompt, setPrompt] = useState("");
  const [resumeFromNodeId, setResumeFromNodeId] = useState<string | null>(null);
  const [outputKind, setOutputKind] = useState<OutputKind>("summary");
  const promptRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (open) {
      setPrompt("");
      setResumeFromNodeId(presetResumeFromNodeId ?? null);
      setOutputKind(presetOutputKind);
      window.setTimeout(() => promptRef.current?.focus(), 0);
    }
  }, [open, presetResumeFromNodeId, presetOutputKind]);

  const indexed = useMemo(() => {
    return resumeOptions.map((node, idx) => ({ node, idx }));
  }, [resumeOptions]);

  if (!open) return null;

  const canSubmit = prompt.trim().length > 0;

  const submit = () => {
    if (!canSubmit) return;
    onLaunch(prompt.trim(), resumeFromNodeId, outputKind);
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-surface-scrim/60 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-[640px] max-w-[95vw] flex-col rounded-xl border border-line bg-surface-raised shadow-modal">
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            <div className="font-display text-sm font-semibold text-ink-strong">
              Launch node
            </div>
            <div className="text-[11px] text-ink-muted">
              Each launch creates a new agent node. Pick a source to resume from a prior conversation.
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

        <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-5 py-4 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Prompt
            </span>
            <textarea
              ref={promptRef}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  submit();
                }
              }}
              rows={Math.min(12, Math.max(4, prompt.split("\n").length + 1))}
              placeholder="What should the agent do?"
              className="resize-none rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
            />
            <span className="text-[10px] text-ink-subtle">
              ⌘/Ctrl + Enter to launch
            </span>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Resume from <span className="text-ink-subtle/70">(optional)</span>
            </span>
            <select
              value={resumeFromNodeId ?? ""}
              onChange={(e) => setResumeFromNodeId(e.target.value || null)}
              className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong focus:border-brand focus:outline-none"
            >
              <option value="">(start fresh)</option>
              {indexed.map(({ node, idx }) => (
                <option key={node.id} value={node.id}>
                  {idx + 1}. {node.id.slice(0, 8)} —{" "}
                  {(node.summary || node.prompt || "(no prompt)").slice(0, 60)}
                </option>
              ))}
            </select>
            {indexed.length === 0 && (
              <span className="text-[10px] text-ink-subtle">
                No resumable nodes yet — only terminal nodes with a provider session can be resumed.
              </span>
            )}
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
              Output contract
            </span>
            <select
              value={outputKind}
              onChange={(e) => setOutputKind(e.target.value as OutputKind)}
              className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-sm text-ink-strong focus:border-brand focus:outline-none"
            >
              <option value="summary">summary</option>
              <option value="interface">interface</option>
              <option value="review_brief">review</option>
              <option value="freeform">freeform</option>
            </select>
            <span className="text-[10px] text-ink-subtle">
              summary writes a markdown result file; interface writes machine-readable JSON; review writes a brief and then auto-spawns a human-review gate node.
            </span>
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
            onClick={submit}
            className="rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
          >
            Launch
          </button>
        </div>
      </div>
    </div>
  );
}
