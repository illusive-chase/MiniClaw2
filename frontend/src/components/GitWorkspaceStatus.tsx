import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { GitFileStatus, GitStatus } from "../types";

type GitWorkspaceStatusProps = {
  status: GitStatus | null;
  action: "commit" | "review" | "pull" | "push" | null;
  canCommit: boolean;
  canPull: boolean;
  canPush: boolean;
  onRefresh: () => Promise<void> | void;
  onCommit: () => void;
  onPull: () => void;
  onPush: () => void;
};

const CONFLICT_CODES = new Set(["DD", "AU", "UD", "UA", "DU", "AA", "UU"]);
const POPOVER_MAX_WIDTH = 512;
const POPOVER_VIEWPORT_GAP = 16;

type PopoverPosition = {
  left: number;
  top: number;
  originX: number;
};

export function GitWorkspaceStatus({
  status,
  action,
  canCommit,
  canPull,
  canPush,
  onRefresh,
  onCommit,
  onPull,
  onPush,
}: GitWorkspaceStatusProps) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<PopoverPosition | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const files = status?.files ?? [];
  const totals = useMemo(
    () =>
      files.reduce(
        (sum, file) => ({
          additions: sum.additions + (file.binary ? 0 : file.additions),
          deletions: sum.deletions + (file.binary ? 0 : file.deletions),
          binaries: sum.binaries + (file.binary ? 1 : 0),
        }),
        { additions: 0, deletions: 0, binaries: 0 },
      ),
    [files],
  );

  const updatePopoverPosition = useCallback(() => {
    const trigger = buttonRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const width = Math.min(
      POPOVER_MAX_WIDTH,
      Math.max(0, window.innerWidth - POPOVER_VIEWPORT_GAP * 2),
    );
    const maxLeft = Math.max(
      POPOVER_VIEWPORT_GAP,
      window.innerWidth - width - POPOVER_VIEWPORT_GAP,
    );
    const left = Math.min(Math.max(rect.left, POPOVER_VIEWPORT_GAP), maxLeft);
    setPosition({
      left,
      top: rect.bottom + 8,
      originX: Math.min(Math.max(rect.left + rect.width / 2 - left, 14), width - 14),
    });
  }, []);

  useEffect(() => {
    if (!open) return;
    const closeOnPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    const reposition = () => updatePopoverPosition();
    document.addEventListener("pointerdown", closeOnPointerDown);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => {
      document.removeEventListener("pointerdown", closeOnPointerDown);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [open, updatePopoverPosition]);

  const title = status?.is_repo
    ? `${status.branch ?? "detached"}${status.upstream ? ` · upstream ${status.upstream}` : ""}`
    : "Not a Git repository";
  const summary = status?.is_repo
    ? `git ${status.dirty_count ? status.dirty_count : "clean"}${status.ahead || status.behind ? ` ↑${status.ahead ?? 0} ↓${status.behind ?? 0}` : ""}`
    : "git —";

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        disabled={!status?.is_repo}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => {
          if (open) {
            setOpen(false);
            return;
          }
          updatePopoverPosition();
          setOpen(true);
          void onRefresh();
        }}
        className={
          "inline-flex items-center gap-1 rounded-sm px-1 py-0.5 transition " +
          (status?.is_repo
            ? "text-ink-muted hover:bg-surface-sunken hover:text-ink-strong"
            : "cursor-default text-ink-subtle")
        }
        title={title}
      >
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-current" />
        {summary}
      </button>

      {open && position && status?.is_repo && (
        <div
          role="dialog"
          aria-label="Git working tree status"
          className="git-status-popover-enter fixed z-50 w-[min(32rem,calc(100vw-2rem))] font-sans text-ink"
          style={{
            left: position.left,
            top: position.top,
            "--git-popover-origin-x": `${position.originX}px`,
          } as CSSProperties}
        >
          <span
            aria-hidden="true"
            className="absolute -top-1.5 z-10 h-3 w-3 -translate-x-1/2 rotate-45 border-l border-t border-line bg-surface-raised"
            style={{ left: position.originX }}
          />
          <div className="overflow-hidden rounded-md border border-line bg-surface-raised shadow-modal">
            <div className="flex items-start justify-between gap-4 border-b border-line px-3.5 py-3">
              <div className="min-w-0">
                <div className="text-xs font-semibold text-ink-strong">Working tree</div>
                <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[10.5px] text-ink-muted">
                  <span className="truncate font-mono">{status.branch ?? "detached"}</span>
                  {status.upstream && (
                    <>
                      <span className="text-line-strong">·</span>
                      <span className="truncate font-mono">{status.upstream}</span>
                    </>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <button
                  type="button"
                  onClick={() => void onRefresh()}
                  className="inline-flex h-7 w-7 items-center justify-center rounded-sm text-base text-ink-muted transition hover:bg-surface-sunken hover:text-ink-strong"
                  aria-label="Refresh Git status"
                  title="Refresh Git status"
                >
                  ↻
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    buttonRef.current?.focus();
                  }}
                  className="inline-flex h-7 w-7 items-center justify-center rounded-sm text-lg text-ink-muted transition hover:bg-surface-sunken hover:text-ink-strong"
                  aria-label="Close Git status"
                  title="Close"
                >
                  ×
                </button>
              </div>
            </div>

            <div className="flex items-center justify-between gap-3 border-b border-line bg-surface-sunken/60 px-3.5 py-2 text-[10.5px]">
              <span className="text-ink-muted">
                {files.length} {files.length === 1 ? "file" : "files"}
                {totals.binaries > 0 ? ` · ${totals.binaries} binary` : ""}
              </span>
              <span className="shrink-0 font-mono">
                <span className="text-state-review">+{totals.additions}</span>
                <span className="ml-2 text-state-error">−{totals.deletions}</span>
              </span>
            </div>

            <div className="max-h-[min(26rem,60vh)] overflow-y-auto">
              {files.length === 0 ? (
                <div className="px-4 py-8 text-center text-xs text-ink-muted">
                  Working tree clean
                </div>
              ) : (
                files.map((file) => <GitFileRow key={`${file.old_path ?? ""}\0${file.path}`} file={file} />)
              )}
            </div>

            <div className="grid grid-cols-3 gap-2 border-t border-line bg-surface-sunken/60 p-2.5">
              <GitActionButton
                label="Commit"
                pending={action === "commit"}
                disabled={!canCommit}
                title="Commit working tree"
                onClick={() => {
                  setOpen(false);
                  onCommit();
                }}
              />
              <GitActionButton
                label="Pull"
                pending={action === "pull"}
                disabled={!canPull}
                title="Pull with rebase"
                onClick={onPull}
              />
              <GitActionButton
                label="Push"
                pending={action === "push"}
                disabled={!canPush}
                title="Push upstream"
                onClick={onPush}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function GitActionButton({
  label,
  pending,
  disabled,
  title,
  onClick,
}: {
  label: string;
  pending: boolean;
  disabled: boolean;
  title: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex h-8 items-center justify-center rounded-sm border border-line bg-surface-raised px-2 text-[11px] font-medium text-ink-muted transition hover:border-line-strong hover:bg-surface hover:text-ink-strong disabled:cursor-not-allowed disabled:opacity-40"
      title={title}
    >
      {pending ? "…" : label}
    </button>
  );
}

function GitFileRow({ file }: { file: GitFileStatus }) {
  const code = displayCode(file);
  const description = statusDescription(file);
  const tone = statusTone(file);
  return (
    <div className="grid grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-2.5 border-b border-line/70 px-3.5 py-2.5 last:border-b-0">
      <span
        className={`inline-flex h-6 w-8 items-center justify-center rounded-sm bg-surface-sunken font-mono text-[10.5px] font-semibold ${tone}`}
        title={`${description} (${code.trim() || code})`}
      >
        {code}
      </span>
      <div className="min-w-0">
        <div className="truncate font-mono text-[11px] text-ink-strong" title={file.path}>
          {file.path}
        </div>
        <div className="mt-0.5 truncate text-[10px] text-ink-muted" title={file.old_path ?? description}>
          {file.old_path ? `${file.old_path} → ${description}` : description}
        </div>
      </div>
      <div className="min-w-[4.75rem] text-right font-mono text-[10.5px]">
        {file.binary ? (
          <span className="text-ink-muted">binary</span>
        ) : (
          <>
            <span className="text-state-review">+{file.additions}</span>
            <span className="ml-1.5 text-state-error">−{file.deletions}</span>
          </>
        )}
      </div>
    </div>
  );
}

function displayCode(file: GitFileStatus): string {
  if (file.index_status === "?" || file.worktree_status === "?") return "??";
  const index = file.index_status === "." ? " " : file.index_status;
  const worktree = file.worktree_status === "." ? " " : file.worktree_status;
  return `${index}${worktree}`;
}

function isConflict(file: GitFileStatus): boolean {
  return (
    CONFLICT_CODES.has(`${file.index_status}${file.worktree_status}`) ||
    file.index_status === "U" ||
    file.worktree_status === "U"
  );
}

function statusDescription(file: GitFileStatus): string {
  if (file.index_status === "?" || file.worktree_status === "?") return "Untracked";
  if (isConflict(file)) return "Merge conflict";
  const states: string[] = [];
  const indexLabel = statusLabel(file.index_status);
  const worktreeLabel = statusLabel(file.worktree_status);
  if (indexLabel) states.push(`Staged ${indexLabel.toLowerCase()}`);
  if (worktreeLabel) states.push(worktreeLabel);
  return states.join(" · ") || "Changed";
}

function statusLabel(code: string): string | null {
  if (code === "M") return "Modified";
  if (code === "A") return "Added";
  if (code === "D") return "Deleted";
  if (code === "R") return "Renamed";
  if (code === "C") return "Copied";
  if (code === "T") return "Type changed";
  return null;
}

function statusTone(file: GitFileStatus): string {
  if (isConflict(file) || file.index_status === "D" || file.worktree_status === "D") {
    return "text-state-error";
  }
  if (file.index_status === "?" || file.worktree_status === "?") return "text-state-waiting";
  if (file.index_status === "A" || file.index_status === "R" || file.index_status === "C") {
    return "text-state-review";
  }
  return "text-brand-ink";
}
