import { useCallback, useEffect, useRef, useState } from "react";
import { deleteSession, listSessions, renameSession } from "../api";
import type { SessionInfo } from "../types";
import { TestsPanel } from "./TestsPanel";
import { ThemeToggle } from "./ThemeToggle";

type Props = {
  onOpen: (session: SessionInfo) => void;
  onCreate: () => void;
  /** scenario runner kicks off a new project — open the result */
  onScenarioLaunched?: (session: SessionInfo, scenarioName: string) => void;
};

export function ProjectsLanding({ onOpen, onCreate, onScenarioLaunched }: Props) {
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testsOpen, setTestsOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const next = await listSessions();
      next.sort((a, b) => b.created_at - a.created_at);
      setSessions(next);
    } catch (err) {
      setError(String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => void refresh(), 10_000);
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
    };
  }, [refresh]);

  const onRename = useCallback(async (id: string, name: string) => {
    const trimmed = name.trim();
    const updated = await renameSession(id, trimmed);
    setSessions((prev) =>
      prev ? prev.map((s) => (s.id === id ? { ...s, name: updated.name } : s)) : prev,
    );
  }, []);

  const onDelete = useCallback(
    async (id: string) => {
      setSessions((prev) => (prev ? prev.filter((s) => s.id !== id) : prev));
      try {
        await deleteSession(id);
      } catch (err) {
        setError(String(err));
        void refresh();
      }
    },
    [refresh],
  );

  return (
    <div className="flex h-full flex-col bg-surface text-ink">
      <header className="flex items-center justify-between gap-4 border-b border-line bg-surface-raised px-8 py-5">
        <div className="min-w-0">
          <div className="flex items-baseline gap-2">
            <span className="inline-block h-2 w-2 rounded-full bg-brand" />
            <h1 className="font-display text-[22px] font-semibold tracking-tight text-ink-strong">
              Projects
            </h1>
          </div>
          <p className="mt-1 max-w-xl text-[12px] leading-relaxed text-ink-muted">
            Each project pins a long-lived git workspace. Open one to inspect its node
            timeline, or start a new working tree.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setTestsOpen(true)}
            className="inline-flex h-9 items-center gap-1.5 rounded-md border border-line bg-surface-raised px-3 text-[12.5px] font-medium text-ink-muted shadow-card transition hover:border-line-strong hover:text-ink"
            title="Run a packaged scenario test"
          >
            Tests
          </button>
          <button
            type="button"
            onClick={onCreate}
            className="inline-flex h-9 items-center gap-1.5 rounded-md bg-brand px-4 text-sm font-medium text-white shadow-card transition hover:brightness-[0.95]"
          >
            <span className="text-base leading-none">+</span> New project
          </button>
          <ThemeToggle />
        </div>
      </header>

      <div className="flex-1 overflow-y-auto bg-surface-sunken px-8 py-7">
        {error && (
          <div className="mb-4 rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-xs text-state-error">
            {error}
          </div>
        )}

        {sessions === null && !error && (
          <div className="text-xs text-ink-muted">Loading…</div>
        )}

        {sessions && sessions.length === 0 && (
          <div className="mx-auto flex max-w-2xl flex-col items-start gap-4 rounded-xl border border-dashed border-line bg-surface-raised px-7 py-9 shadow-card">
            <div className="font-display text-lg font-semibold tracking-tight text-ink-strong">
              No projects yet
            </div>
            <p className="text-sm text-ink-muted">
              Start your first project to launch agents against a working tree.
              Projects persist across sessions; each node within is a single
              agent turn with its own provider context and git diff.
            </p>
            <button
              type="button"
              onClick={onCreate}
              className="mt-2 inline-flex h-9 items-center gap-1.5 rounded-md bg-brand px-4 text-sm font-medium text-white shadow-card transition hover:brightness-[0.95]"
            >
              <span className="text-base leading-none">+</span> Create your first project
            </button>
          </div>
        )}

        {sessions && sessions.length > 0 && (
          <>
            <div className="mb-3 flex items-center justify-between">
              <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
                {sessions.length} project{sessions.length === 1 ? "" : "s"}
              </div>
              <div className="font-mono text-[10px] text-ink-subtle">
                sorted by recent
              </div>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {sessions.map((s) => (
                <ProjectCard
                  key={s.id}
                  session={s}
                  onOpen={() => onOpen(s)}
                  onRename={(name) => onRename(s.id, name)}
                  onDelete={() => onDelete(s.id)}
                />
              ))}
            </div>
          </>
        )}
      </div>

      {testsOpen && (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center bg-surface-scrim/60 backdrop-blur-sm"
          onClick={() => setTestsOpen(false)}
        >
          <div
            className="flex max-h-[90vh] w-[720px] max-w-[95vw] flex-col overflow-hidden rounded-xl border border-line bg-surface-raised shadow-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
              <div className="min-w-0">
                <div className="font-display text-sm font-semibold text-ink-strong">
                  Tests
                </div>
                <div className="text-[11px] text-ink-muted">
                  Run a packaged scenario; opens the resulting project on launch.
                </div>
              </div>
              <button
                type="button"
                onClick={() => setTestsOpen(false)}
                className="rounded px-2 py-1 text-[11px] font-medium text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
              >
                Esc
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">
              <TestsPanel
                onLaunched={(s, name) => {
                  setTestsOpen(false);
                  if (onScenarioLaunched) onScenarioLaunched(s, name);
                  else onOpen(s);
                }}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ProjectCard({
  session,
  onOpen,
  onRename,
  onDelete,
}: {
  session: SessionInfo;
  onOpen: () => void;
  onRename: (name: string) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(session.name ?? "");
  const [saving, setSaving] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (editing) {
      setDraft(session.name ?? "");
      window.setTimeout(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      }, 0);
    }
  }, [editing, session.name]);

  const commitRename = async () => {
    if (saving) return;
    const next = draft.trim();
    if (next === (session.name ?? "")) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      await onRename(next);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      window.setTimeout(() => setConfirmingDelete(false), 3500);
      return;
    }
    await onDelete();
  };

  const handleCardClick = () => {
    if (editing || confirmingDelete) return;
    onOpen();
  };

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={handleCardClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          handleCardClick();
        }
      }}
      className="group relative flex cursor-pointer flex-col gap-2.5 overflow-hidden rounded-lg border border-line bg-surface-raised px-4 py-3.5 shadow-card transition hover:-translate-y-0.5 hover:border-line-strong hover:shadow-raised"
    >
      {/* hairline left accent on hover */}
      <span
        className="pointer-events-none absolute inset-y-0 left-0 w-[2px] origin-top scale-y-0 bg-brand transition-transform duration-200 group-hover:scale-y-100"
        aria-hidden="true"
      />

      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          {editing ? (
            <input
              ref={inputRef}
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onClick={(e) => e.stopPropagation()}
              onBlur={() => void commitRename()}
              onKeyDown={(e) => {
                e.stopPropagation();
                if (e.key === "Enter") {
                  e.preventDefault();
                  void commitRename();
                } else if (e.key === "Escape") {
                  e.preventDefault();
                  setEditing(false);
                }
              }}
              disabled={saving}
              placeholder="(unnamed)"
              className="w-full rounded border border-line bg-surface-sunken px-2 py-1 text-sm text-ink-strong focus:border-brand focus:outline-none"
            />
          ) : (
            <div className="truncate font-display text-[15px] font-semibold tracking-tight text-ink-strong">
              {session.name?.trim() ? (
                session.name
              ) : (
                <span className="font-sans font-normal italic text-ink-subtle">
                  (unnamed)
                </span>
              )}
            </div>
          )}
        </div>
        <div className="flex flex-none items-center gap-1 opacity-0 transition group-hover:opacity-100">
          {!editing && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setEditing(true);
              }}
              title="Rename"
              className="rounded p-1 text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
            >
              <PencilIcon />
            </button>
          )}
          <button
            type="button"
            onClick={handleDelete}
            title={confirmingDelete ? "Click again to confirm" : "Delete"}
            className={
              "rounded p-1 transition hover:bg-surface-sunken " +
              (confirmingDelete
                ? "text-state-error ring-1 ring-state-error/40"
                : "text-ink-muted hover:text-state-error")
            }
          >
            <TrashIcon />
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
        <span className="rounded border border-line bg-surface-sunken px-1.5 py-0.5 uppercase tracking-[0.12em] text-ink-muted">
          {session.provider ?? "claude"}
        </span>
        {session.scenario_name && (
          <span className="rounded border border-brand/30 bg-brand-soft px-1.5 py-0.5 text-brand-ink dark:text-brand">
            {session.scenario_name}
          </span>
        )}
        {session.temporary && (
          <span className="rounded border border-state-waiting/30 bg-state-waiting-soft px-1.5 py-0.5 text-state-waiting">
            temp
          </span>
        )}
      </div>

      <div className="mt-auto flex items-center justify-between text-[11px] text-ink-subtle">
        <span>
          {session.turns} node{session.turns === 1 ? "" : "s"} ·{" "}
          {formatRelative(session.created_at)}
        </span>
        <span className="font-mono text-ink-subtle">{session.id.slice(0, 8)}</span>
      </div>
    </div>
  );
}

function formatRelative(ts: number): string {
  const now = Date.now() / 1000;
  const delta = now - ts;
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  if (delta < 86400 * 7) return `${Math.floor(delta / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

function PencilIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M11.013 1.427a1.75 1.75 0 0 1 2.474 0l1.086 1.086a1.75 1.75 0 0 1 0 2.474l-8.61 8.61c-.21.21-.47.364-.756.445l-3.251.93a.75.75 0 0 1-.927-.928l.929-3.25c.081-.286.235-.547.445-.758l8.61-8.61Zm1.414 1.06a.25.25 0 0 0-.354 0L10.811 3.75l1.439 1.44 1.263-1.263a.25.25 0 0 0 0-.354l-1.086-1.086ZM11.189 6.25 9.75 4.811l-6.286 6.287a.25.25 0 0 0-.064.108l-.558 1.953 1.953-.558a.249.249 0 0 0 .108-.064l6.286-6.287Z" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M11 1.75V3h2.25a.75.75 0 0 1 0 1.5H2.75a.75.75 0 0 1 0-1.5H5V1.75C5 .784 5.784 0 6.75 0h2.5C10.216 0 11 .784 11 1.75ZM4.496 6.675l.66 6.6a.25.25 0 0 0 .249.225h5.19a.25.25 0 0 0 .249-.225l.66-6.6a.75.75 0 0 1 1.492.149l-.66 6.6A1.748 1.748 0 0 1 10.595 15h-5.19a1.75 1.75 0 0 1-1.741-1.575l-.66-6.6a.75.75 0 1 1 1.492-.15ZM6.5 1.75V3h3V1.75a.25.25 0 0 0-.25-.25h-2.5a.25.25 0 0 0-.25.25Z" />
    </svg>
  );
}
