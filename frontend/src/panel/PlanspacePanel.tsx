import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  getPlanspaceStatus,
  patchPlanspaceStatus,
  type PlanspaceStatusOp,
  type PlanspaceStatusView,
} from "../api";

export type PlanspacePanelProps = {
  sessionId: string;
  planspaceId: string;
};

/**
 * Slot-aware viewer + editor for one planspace's STATUS.md.
 *
 * The structured slots (goal, current_state, open_questions, decisions,
 * out_of_scope) get their own form controls. The free-form body is shown
 * as read-only markdown — node commits accrue there, and editing it by
 * hand would lose the per-node attribution.
 */
export function PlanspacePanel({ sessionId, planspaceId }: PlanspacePanelProps) {
  const [view, setView] = useState<PlanspaceStatusView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draftCurrentState, setDraftCurrentState] = useState("");
  const [newQuestion, setNewQuestion] = useState("");
  const [newDecision, setNewDecision] = useState("");
  const [newOutOfScope, setNewOutOfScope] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getPlanspaceStatus(sessionId, planspaceId)
      .then((v) => {
        if (cancelled) return;
        setView(v);
        setDraftCurrentState(v.status.current_state);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, planspaceId]);

  const applyOps = async (operations: PlanspaceStatusOp[]) => {
    if (saving || operations.length === 0) return;
    setSaving(true);
    setError(null);
    try {
      const next = await patchPlanspaceStatus(sessionId, planspaceId, operations);
      setView(next);
      setDraftCurrentState(next.status.current_state);
      setNewQuestion("");
      setNewDecision("");
      setNewOutOfScope("");
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  if (loading || !view) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-sm text-ink-muted">
        {error ? `Failed to load planspace: ${error}` : "Loading planspace…"}
      </div>
    );
  }

  const status = view.status;
  const dirtyCurrentState =
    draftCurrentState.trim() !== status.current_state.trim();

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-line bg-surface-raised px-4 py-3">
        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          Planspace
        </div>
        <h2 className="mt-1 line-clamp-2 font-display text-[15px] font-semibold leading-snug text-ink-strong">
          {view.title}
        </h2>
        <div className="mt-0.5 font-mono text-[10px] text-ink-subtle">
          {planspaceId}
          {view.color && (
            <span className="ml-2 inline-flex items-center gap-1">
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: `var(--planspace-${view.color}, currentColor)` }}
              />
              {view.color}
            </span>
          )}
        </div>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto bg-surface px-4 py-4 text-sm">
        {error && (
          <div className="rounded-md border border-state-error/30 bg-state-error-soft p-2 text-xs text-state-error">
            {error}
          </div>
        )}

        <section>
          <SectionLabel>Goal</SectionLabel>
          <div className="mt-1 whitespace-pre-wrap rounded-md border border-line bg-surface-raised px-3 py-2 text-[13px] text-ink-strong">
            {status.goal || "—"}
          </div>
        </section>

        <section>
          <SectionLabel>Current state</SectionLabel>
          <textarea
            value={draftCurrentState}
            onChange={(e) => setDraftCurrentState(e.target.value)}
            rows={4}
            className="mt-1 w-full resize-none rounded-md border border-line bg-surface-sunken px-3 py-2 text-[13px] leading-relaxed text-ink-strong focus:border-brand focus:outline-none"
          />
          {dirtyCurrentState && (
            <div className="mt-1 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setDraftCurrentState(status.current_state)}
                className="rounded border border-line bg-surface px-2 py-0.5 text-[11px] text-ink-muted hover:text-ink"
              >
                Reset
              </button>
              <button
                type="button"
                disabled={saving}
                onClick={() =>
                  applyOps([
                    {
                      operation: "rewrite_current_state",
                      text: draftCurrentState.trim(),
                    },
                  ])
                }
                className="rounded bg-brand px-2.5 py-0.5 text-[11px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
              >
                Save
              </button>
            </div>
          )}
        </section>

        <SlotList
          label="Open questions"
          entries={status.open_questions.map((q) => ({
            primary: q.summary,
            secondary: `${q.id}${q.raised_at ? ` · ${q.raised_at}` : ""}${
              q.raised_by ? ` · ${q.raised_by}` : ""
            }`,
            removeOp: { operation: "remove_open_question", id: q.id },
          }))}
          inputValue={newQuestion}
          onInputChange={setNewQuestion}
          onAdd={() =>
            applyOps([
              { operation: "add_open_question", summary: newQuestion.trim() },
            ])
          }
          addDisabled={saving || newQuestion.trim().length === 0}
          onRemove={(op) => applyOps([op])}
        />

        <SlotList
          label="Decisions"
          entries={status.decisions.map((d) => ({
            primary: d.summary,
            secondary: `${d.id}${d.decided_at ? ` · ${d.decided_at}` : ""}${
              d.decided_by ? ` · ${d.decided_by}` : ""
            }`,
            removeOp: { operation: "remove_decision", id: d.id },
          }))}
          inputValue={newDecision}
          onInputChange={setNewDecision}
          onAdd={() =>
            applyOps([
              { operation: "add_decision", summary: newDecision.trim() },
            ])
          }
          addDisabled={saving || newDecision.trim().length === 0}
          onRemove={(op) => applyOps([op])}
        />

        <SlotList
          label="Out of scope"
          entries={status.out_of_scope.map((text, index) => ({
            primary: text,
            secondary: `#${index + 1}`,
            removeOp: { operation: "remove_out_of_scope", index },
          }))}
          inputValue={newOutOfScope}
          onInputChange={setNewOutOfScope}
          onAdd={() =>
            applyOps([
              { operation: "add_out_of_scope", summary: newOutOfScope.trim() },
            ])
          }
          addDisabled={saving || newOutOfScope.trim().length === 0}
          onRemove={(op) => applyOps([op])}
        />

        <section>
          <SectionLabel>Notes</SectionLabel>
          <div className="md-prose mt-1 rounded-md border border-line bg-surface-raised px-3 py-3 text-[12.5px] leading-relaxed text-ink-strong">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {status.body || "_No notes yet._"}
            </ReactMarkdown>
          </div>
        </section>
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
      {children}
    </div>
  );
}

type SlotEntry = {
  primary: string;
  secondary: string;
  removeOp: PlanspaceStatusOp;
};

function SlotList({
  label,
  entries,
  inputValue,
  onInputChange,
  onAdd,
  addDisabled,
  onRemove,
}: {
  label: string;
  entries: SlotEntry[];
  inputValue: string;
  onInputChange: (v: string) => void;
  onAdd: () => void;
  addDisabled: boolean;
  onRemove: (op: PlanspaceStatusOp) => void;
}) {
  return (
    <section>
      <SectionLabel>{label}</SectionLabel>
      <ul className="mt-1 space-y-1.5">
        {entries.length === 0 && (
          <li className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[12px] text-ink-muted">
            None yet.
          </li>
        )}
        {entries.map((entry, idx) => (
          <li
            key={`${label}-${idx}-${entry.secondary}`}
            className="flex items-start gap-2 rounded-md border border-line bg-surface-raised px-3 py-2"
          >
            <div className="min-w-0 flex-1">
              <div className="whitespace-pre-wrap text-[13px] text-ink-strong">
                {entry.primary}
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-ink-subtle">
                {entry.secondary}
              </div>
            </div>
            <button
              type="button"
              onClick={() => onRemove(entry.removeOp)}
              className="flex-none rounded border border-line bg-surface px-1.5 py-0.5 text-[10px] text-ink-muted transition hover:border-state-error hover:text-state-error"
              title="Remove this entry"
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <div className="mt-2 flex gap-2">
        <input
          type="text"
          value={inputValue}
          onChange={(e) => onInputChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !addDisabled) {
              e.preventDefault();
              onAdd();
            }
          }}
          placeholder={`Add ${label.toLowerCase()}…`}
          className="flex-1 rounded-md border border-line bg-surface-sunken px-3 py-1.5 text-[12.5px] text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
        />
        <button
          type="button"
          disabled={addDisabled}
          onClick={onAdd}
          className="rounded-md bg-brand px-2.5 py-1.5 text-[11px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
        >
          Add
        </button>
      </div>
    </section>
  );
}
