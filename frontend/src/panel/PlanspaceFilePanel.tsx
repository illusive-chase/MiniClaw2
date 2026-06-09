import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

import {
  getPlanspaceStatus,
  getSessionFile,
  patchPlanspaceStatus,
  type PlanspaceStatusOp,
  type PlanspaceStatusView,
} from "../api";
import type { NodeInfo, SessionFile, SessionFileRole } from "../types";

export type PlanspaceFilePanelProps = {
  sessionId: string;
  role: SessionFileRole;
  planspaceId?: string | null;
  loadedByNodeIds: string[];
  nodesById: Map<string, NodeInfo>;
  onSelectConsumer: (nodeId: string) => void;
  reloadVersion?: number;
};

export function PlanspaceFilePanel({
  sessionId,
  role,
  planspaceId,
  loadedByNodeIds,
  nodesById,
  onSelectConsumer,
  reloadVersion = 0,
}: PlanspaceFilePanelProps) {
  const [file, setFile] = useState<SessionFile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    setLoading(true);
    setError(null);
    getSessionFile(sessionId, role, planspaceId)
      .then(setFile)
      .catch((err) => {
        setError(String(err));
        setFile(null);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getSessionFile(sessionId, role, planspaceId)
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
  }, [sessionId, role, planspaceId, reloadVersion]);

  const filename = role === "context" ? "CONTEXT.md" : role === "plan" ? "PLAN.md" : "STATUS.md";

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-line bg-surface-raised px-4 py-3">
        <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
          {role === "context" ? "Project notes" : "Direction notebook"}
        </div>
        <h2 className="mt-1 truncate font-display text-[15px] font-semibold leading-snug text-ink-strong">
          {filename}
        </h2>
        <div className="mt-1 truncate font-mono text-[10.5px] text-ink-muted">
          {planspaceId ?? file?.path ?? "project-root"}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto bg-surface px-4 py-3 text-sm">
        <section className="mb-4">
          <SectionLabel>What is this?</SectionLabel>
          <div className="mt-1 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[12.5px] text-ink-strong">
            {descriptionForRole(role)}
          </div>
          {file && (
            <div className="mt-1 font-mono text-[10.5px] text-ink-subtle">
              Last updated {new Date(file.mtime * 1000).toLocaleString()}
              {" · "}
              {writerLabel(file)}
            </div>
          )}
        </section>

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

        {role === "status" && planspaceId && (
          <section className="mb-4">
            <details className="overflow-hidden rounded-md border border-line bg-surface-sunken">
              <summary className="cursor-pointer px-3 py-2 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-muted hover:text-ink">
                Edit slots
              </summary>
              <div className="border-t border-line px-3 py-3">
                <StatusSlotEditor
                  sessionId={sessionId}
                  planspaceId={planspaceId}
                  onChanged={refresh}
                />
              </div>
            </details>
          </section>
        )}

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

function StatusSlotEditor({
  sessionId,
  planspaceId,
  onChanged,
}: {
  sessionId: string;
  planspaceId: string;
  onChanged: () => void;
}) {
  const [view, setView] = useState<PlanspaceStatusView | null>(null);
  const [draftCurrentState, setDraftCurrentState] = useState("");
  const [newQuestion, setNewQuestion] = useState("");
  const [newDecision, setNewDecision] = useState("");
  const [newOutOfScope, setNewOutOfScope] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getPlanspaceStatus(sessionId, planspaceId)
      .then((next) => {
        if (cancelled) return;
        setView(next);
        setDraftCurrentState(next.status.current_state);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
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
      onChanged();
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  if (!view) {
    return (
      <div className="text-[12px] text-ink-muted">
        {error ? `Failed to load slots: ${error}` : "Loading slots..."}
      </div>
    );
  }

  const status = view.status;
  const dirtyCurrentState = draftCurrentState.trim() !== status.current_state.trim();

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-md border border-state-error/30 bg-state-error-soft p-2 text-xs text-state-error">
          {error}
        </div>
      )}
      <section>
        <SectionLabel>Current state</SectionLabel>
        <textarea
          value={draftCurrentState}
          onChange={(event) => setDraftCurrentState(event.target.value)}
          rows={4}
          className="mt-1 w-full resize-none rounded-md border border-line bg-surface px-3 py-2 text-[13px] leading-relaxed text-ink-strong focus:border-brand focus:outline-none"
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
                applyOps([{ operation: "rewrite_current_state", text: draftCurrentState.trim() }])
              }
              className="rounded bg-brand px-2.5 py-0.5 text-[11px] font-medium text-white shadow-card transition hover:brightness-[0.95] disabled:opacity-40"
            >
              Save
            </button>
          </div>
        )}
      </section>

      <CompactSlotList
        label="Open questions"
        entries={status.open_questions.map((q) => ({
          primary: q.summary,
          secondary: q.id,
          removeOp: { operation: "remove_open_question", id: q.id },
        }))}
        inputValue={newQuestion}
        onInputChange={setNewQuestion}
        onAdd={() => applyOps([{ operation: "add_open_question", summary: newQuestion.trim() }])}
        addDisabled={saving || newQuestion.trim().length === 0}
        onRemove={(op) => applyOps([op])}
      />

      <CompactSlotList
        label="Decisions"
        entries={status.decisions.map((d) => ({
          primary: d.summary,
          secondary: d.id,
          removeOp: { operation: "remove_decision", id: d.id },
        }))}
        inputValue={newDecision}
        onInputChange={setNewDecision}
        onAdd={() => applyOps([{ operation: "add_decision", summary: newDecision.trim() }])}
        addDisabled={saving || newDecision.trim().length === 0}
        onRemove={(op) => applyOps([op])}
      />

      <CompactSlotList
        label="Out of scope"
        entries={status.out_of_scope.map((text, index) => ({
          primary: text,
          secondary: `#${index + 1}`,
          removeOp: { operation: "remove_out_of_scope", index },
        }))}
        inputValue={newOutOfScope}
        onInputChange={setNewOutOfScope}
        onAdd={() => applyOps([{ operation: "add_out_of_scope", summary: newOutOfScope.trim() }])}
        addDisabled={saving || newOutOfScope.trim().length === 0}
        onRemove={(op) => applyOps([op])}
      />
    </div>
  );
}

type CompactSlotEntry = {
  primary: string;
  secondary: string;
  removeOp: PlanspaceStatusOp;
};

function CompactSlotList({
  label,
  entries,
  inputValue,
  onInputChange,
  onAdd,
  addDisabled,
  onRemove,
}: {
  label: string;
  entries: CompactSlotEntry[];
  inputValue: string;
  onInputChange: (value: string) => void;
  onAdd: () => void;
  addDisabled: boolean;
  onRemove: (op: PlanspaceStatusOp) => void;
}) {
  return (
    <section>
      <SectionLabel>{label}</SectionLabel>
      <ul className="mt-1 space-y-1">
        {entries.length === 0 && (
          <li className="rounded-md border border-line bg-surface px-3 py-2 text-[12px] text-ink-muted">
            None yet.
          </li>
        )}
        {entries.map((entry, index) => (
          <li
            key={`${label}-${entry.secondary}-${index}`}
            className="flex items-start gap-2 rounded-md border border-line bg-surface px-3 py-2"
          >
            <div className="min-w-0 flex-1">
              <div className="whitespace-pre-wrap text-[12.5px] text-ink-strong">
                {entry.primary}
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-ink-subtle">
                {entry.secondary}
              </div>
            </div>
            <button
              type="button"
              onClick={() => onRemove(entry.removeOp)}
              className="flex-none rounded border border-line bg-surface-sunken px-1.5 py-0.5 text-[10px] text-ink-muted transition hover:border-state-error hover:text-state-error"
            >
              x
            </button>
          </li>
        ))}
      </ul>
      <div className="mt-2 flex gap-2">
        <input
          type="text"
          value={inputValue}
          onChange={(event) => onInputChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !addDisabled) {
              event.preventDefault();
              onAdd();
            }
          }}
          placeholder={`Add ${label.toLowerCase()}...`}
          className="min-w-0 flex-1 rounded-md border border-line bg-surface px-3 py-1.5 text-[12.5px] text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
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

function descriptionForRole(role: SessionFileRole): string {
  if (role === "status") {
    return "Notebook of decisions and open questions for this direction. Updated automatically after each run.";
  }
  if (role === "plan") {
    return "A read-only checklist derived from STATUS. Open questions become checkboxes, decisions appear as completed items.";
  }
  return "Plan-free project handbook. Loaded at the start of every run. Hand-edited; refresh from the project menu.";
}

function writerLabel(file: SessionFile): string {
  const writer = file.last_writer;
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
