import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import {
  createTag,
  deleteSession,
  getSelfUpdate,
  listSessions,
  listTags,
  renameSession,
  updateSessionTags,
  updateTag,
} from "../api";
import { languageLabel } from "../languages";
import type { ModelPreset, SelfUpdateState, SessionInfo, Tag } from "../types";
import { modelPresetLabel } from "../modelPresets";
import { TestsPanel } from "./TestsPanel";
import { ThemeToggle } from "./ThemeToggle";
import { GlobalSettingsModal } from "./GlobalSettingsModal";
import { TagChipRow, TagFilterBar } from "./TagFilterBar";
import { TagEditPopover } from "./TagEditPopover";
import { tagDotClass, type TagColor } from "../tagPalette";
import {
  PROJECT_SORT_LABELS,
  PROJECT_SORT_MODES,
  readProjectSort,
  writeProjectSort,
  type ProjectSortMode,
} from "../projectSort";
import {
  activityAt,
  filterByBinding,
  filterByTags,
  groupByTag,
  recentProjects,
  resolveTags,
  sortFlat,
  tagCounts,
  UNTAGGED_GROUP_ID,
  type BindingFilter,
} from "../projectGrouping";
import type { GlobalState } from "../types";

type Props = {
  onOpen: (session: SessionInfo) => void;
  onCreate: () => void;
  sessions: SessionInfo[] | null;
  setSessions: Dispatch<SetStateAction<SessionInfo[] | null>>;
  tags: Tag[];
  setTags: Dispatch<SetStateAction<Tag[]>>;
  modelPresets: ModelPreset[];
  globalState: GlobalState | null;
  onGlobalStateChanged: (state: GlobalState) => void;
  /** template runner kicks off a new project — open the result */
  onTemplateLaunched?: (session: SessionInfo, templateName: string) => void;
  notificationBell?: ReactNode;
};

export function ProjectsLanding({
  onOpen,
  onCreate,
  sessions,
  setSessions,
  tags,
  setTags,
  modelPresets,
  globalState,
  onGlobalStateChanged,
  onTemplateLaunched,
  notificationBell,
}: Props) {
  const [error, setError] = useState<string | null>(null);
  const [testsOpen, setTestsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [selfUpdate, setSelfUpdate] = useState<SelfUpdateState | null>(null);
  const [sortMode, setSortMode] = useState<ProjectSortMode>(readProjectSort);
  const [bindingFilter, setBindingFilter] = useState<BindingFilter>("bound");
  const [selectedTagIds, setSelectedTagIds] = useState<ReadonlySet<string>>(new Set());
  const [collapsedGroups, setCollapsedGroups] = useState<ReadonlySet<string>>(new Set());

  const refresh = useCallback(async () => {
    try {
      const next = await listSessions();
      setSessions(next);
    } catch (err) {
      setError(String(err));
    }
  }, [setSessions]);

  /* Tags refresh on mount and focus; node activity arrives over the workspace
   * socket and does not force this mostly-static list to reload. */
  const refreshTags = useCallback(async () => {
    try {
      setTags(await listTags());
    } catch (err) {
      setError(String(err));
    }
  }, [setTags]);

  useEffect(() => {
    void refresh();
    void refreshTags();
    const onFocus = () => {
      void refresh();
      void refreshTags();
    };
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
    };
  }, [refresh, refreshTags]);

  /* Source-checkout state is derived from local refs, so this is a cheap local
   * read rather than a poll. Re-derived when settings closes because that is
   * where an explicit remote check can have moved the upstream ref. */
  useEffect(() => {
    if (settingsOpen) return;
    let cancelled = false;
    getSelfUpdate()
      .then((next) => {
        if (!cancelled) setSelfUpdate(next);
      })
      .catch(() => {
        /* The settings panel surfaces the actionable error. */
      });
    return () => {
      cancelled = true;
    };
  }, [settingsOpen]);

  /* A tag deleted on another machine (or in another tab) leaves ids behind in
   * the filter; dropping them keeps the AND filter from matching nothing at all
   * with no visible cause. */
  useEffect(() => {
    setSelectedTagIds((current) => {
      if (current.size === 0) return current;
      const known = new Set(tags.map((tag) => tag.id));
      const next = new Set([...current].filter((id) => known.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [tags]);

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

  const onApplyTags = useCallback(async (id: string, tagIds: string[]) => {
    const updated = await updateSessionTags(id, tagIds);
    setSessions((prev) =>
      prev ? prev.map((s) => (s.id === id ? { ...s, tag_ids: updated.tag_ids } : s)) : prev,
    );
  }, []);

  const onCreateTag = useCallback(async (name: string, color: TagColor) => {
    const created = await createTag(name, color);
    setTags((prev) => [...prev, created]);
    return created;
  }, []);

  const onRecolorTag = useCallback(async (tagId: string, color: TagColor) => {
    const updated = await updateTag(tagId, { color });
    setTags((prev) => prev.map((tag) => (tag.id === tagId ? updated : tag)));
  }, []);

  const onSettingsChanged = useCallback(
    (next: GlobalState) => {
      onGlobalStateChanged(next);
      void refresh();
    },
    [onGlobalStateChanged, refresh],
  );

  const changeSort = useCallback((mode: ProjectSortMode) => {
    setSortMode(mode);
    writeProjectSort(mode);
  }, []);

  const toggleTagFilter = useCallback((tagId: string) => {
    setSelectedTagIds((current) => {
      const next = new Set(current);
      if (next.has(tagId)) next.delete(tagId);
      else next.add(tagId);
      return next;
    });
  }, []);

  const tagsById = useMemo(() => new Map(tags.map((tag) => [tag.id, tag])), [tags]);
  const all = sessions ?? [];
  const recent = useMemo(() => recentProjects(all), [all]);
  const bindingFiltered = useMemo(
    () => filterByBinding(all, bindingFilter),
    [all, bindingFilter],
  );
  const filtered = useMemo(
    () => filterByTags(bindingFiltered, selectedTagIds),
    [bindingFiltered, selectedTagIds],
  );
  const counts = useMemo(() => tagCounts(all), [all]);
  const bindingCounts = useMemo(
    () => ({
      all: all.length,
      bound: all.filter((session) => session.bound_here).length,
      unbound: all.filter((session) => !session.bound_here).length,
    }),
    [all],
  );
  const groups = useMemo(
    () => (sortMode === "grouped" ? groupByTag(filtered, tags) : []),
    [sortMode, filtered, tags],
  );
  const flat = useMemo(
    () => (sortMode === "grouped" ? [] : sortFlat(filtered, sortMode)),
    [sortMode, filtered],
  );
  const remoteBehind = globalState?.sync.remote?.behind ?? 0;
  const remoteRefAt = globalState?.sync.remote?.ref_at;
  const sourceBehind = selfUpdate?.behind ?? 0;
  const sourceRefAt = selfUpdate?.ref_at;
  const describeRef = (at: number | null | undefined) =>
    at ? new Date(at * 1000).toLocaleString() : "未知时间";
  const remoteStatusTitle = [
    remoteBehind > 0
      ? `元数据远端引用落后 ${remoteBehind} 个提交；引用更新于 ${describeRef(remoteRefAt)}`
      : null,
    sourceBehind > 0
      ? `源码远端引用落后 ${sourceBehind} 个提交；引用更新于 ${describeRef(sourceRefAt)}`
      : null,
  ]
    .filter(Boolean)
    .join("\n") || undefined;
  const behindRemote = remoteBehind > 0 || sourceBehind > 0;

  const renderCard = (session: SessionInfo, keyPrefix = "") => (
    <ProjectCard
      key={keyPrefix + session.id}
      session={session}
      tags={resolveTags(session, tagsById)}
      allTags={tags}
      modelPresets={modelPresets}
      onOpen={() => onOpen(session)}
      onRename={(name) => onRename(session.id, name)}
      onDelete={() => onDelete(session.id)}
      onApplyTags={(tagIds) => onApplyTags(session.id, tagIds)}
      onCreateTag={onCreateTag}
      onRecolorTag={onRecolorTag}
    />
  );

  const sortControl = (
    <label className="flex items-center gap-1.5">
      <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
        排序
      </span>
      <select
        value={sortMode}
        onChange={(event) => changeSort(event.target.value as ProjectSortMode)}
        className="rounded border border-line bg-surface-raised px-1.5 py-[3px] text-[11px] text-ink focus:border-brand focus:outline-none"
      >
        {PROJECT_SORT_MODES.map((mode) => (
          <option key={mode} value={mode}>
            {PROJECT_SORT_LABELS[mode]}
          </option>
        ))}
      </select>
    </label>
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
          {notificationBell}
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            className="relative inline-flex h-9 items-center rounded-md border border-line bg-surface-raised px-3 text-[12.5px] font-medium text-ink-muted shadow-card transition hover:border-line-strong hover:text-ink"
            title={remoteStatusTitle}
          >
            Global settings
            {behindRemote ? (
              <span
                className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full border-2 border-surface-raised bg-state-waiting"
                aria-label={remoteStatusTitle}
              />
            ) : null}
          </button>
          <button
            type="button"
            onClick={() => setTestsOpen(true)}
            className="inline-flex h-9 items-center gap-1.5 rounded-md border border-line bg-surface-raised px-3 text-[12.5px] font-medium text-ink-muted shadow-card transition hover:border-line-strong hover:text-ink"
            title="Run a packaged template test"
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
              agent turn with its own model preset and git diff.
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
            {recent.length > 0 && (
              <section className="mb-7">
                <div className="mb-2 flex items-baseline gap-2">
                  <h2 className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-subtle">
                    最近活动
                  </h2>
                  {/* Only worth saying once a filter is actually active — before
                    * that it explains a behavior the user has not seen. */}
                  {selectedTagIds.size > 0 && (
                    <span className="text-[10px] text-ink-subtle/70">不受筛选影响</span>
                  )}
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {recent.map((session) => renderCard(session, "recent-"))}
                </div>
                <div className="mt-7 border-t border-line" />
              </section>
            )}

            <TagFilterBar
              tags={tags}
              selected={selectedTagIds}
              counts={counts}
              onToggle={toggleTagFilter}
              onClear={() => setSelectedTagIds(new Set())}
              sortControl={sortControl}
              bindingFilter={bindingFilter}
              bindingCounts={bindingCounts}
              onBindingFilterChange={setBindingFilter}
              totalLabel={
                <>
                  全部项目 {selectedTagIds.size > 0
                    || bindingFilter !== "all"
                    ? `${filtered.length} / ${all.length}`
                    : all.length}
                </>
              }
            />

            {filtered.length === 0 ? (
              <div className="rounded-lg border border-dashed border-line bg-surface-raised px-5 py-7 text-center">
                <p className="text-[13px] text-ink">
                  当前筛选条件下没有项目。
                </p>
                <p className="mt-1 text-[11.5px] text-ink-muted">
                  可切换绑定状态，或减少选中的 tag。
                </p>
                <button
                  type="button"
                  onClick={() => {
                    setBindingFilter("all");
                    setSelectedTagIds(new Set());
                  }}
                  className="mt-3 inline-flex h-8 items-center rounded-md border border-line bg-surface px-3 text-[12px] font-medium text-ink transition hover:border-brand hover:text-ink-strong"
                >
                  清除筛选
                </button>
              </div>
            ) : sortMode === "grouped" ? (
              <div className="space-y-5">
                {groups.map((group) => {
                  const collapsed = collapsedGroups.has(group.id);
                  return (
                    <section key={group.id}>
                      <button
                        type="button"
                        onClick={() =>
                          setCollapsedGroups((current) => {
                            const next = new Set(current);
                            if (next.has(group.id)) next.delete(group.id);
                            else next.add(group.id);
                            return next;
                          })
                        }
                        aria-expanded={!collapsed}
                        className="group mb-2 flex w-full items-center gap-2 text-left"
                      >
                        <span
                          className={
                            "text-[9px] text-ink-subtle transition-transform "
                            + (collapsed ? "" : "rotate-90")
                          }
                          aria-hidden="true"
                        >
                          ▶
                        </span>
                        <span
                          className={"h-2 w-2 flex-none rounded-full " + tagDotClass(group.color)}
                          aria-hidden="true"
                        />
                        <span
                          className={
                            "text-[12.5px] font-medium "
                            + (group.id === UNTAGGED_GROUP_ID
                              ? "text-ink-muted"
                              : "text-ink-strong")
                          }
                        >
                          {group.label}
                        </span>
                        <span className="font-mono text-[10px] text-ink-subtle">
                          {group.sessions.length}
                        </span>
                        <span className="ml-2 h-px flex-1 bg-line" aria-hidden="true" />
                      </button>
                      {!collapsed && (
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                          {group.sessions.map((session) => renderCard(session, group.id + "-"))}
                        </div>
                      )}
                    </section>
                  );
                })}
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {flat.map((session) => renderCard(session))}
              </div>
            )}
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
                  Run a packaged template; opens the resulting project on launch.
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
                modelPresets={modelPresets}
                onLaunched={(s, name) => {
                  setTestsOpen(false);
                  if (onTemplateLaunched) onTemplateLaunched(s, name);
                  else onOpen(s);
                }}
              />
            </div>
          </div>
        </div>
      )}
      <GlobalSettingsModal
        open={settingsOpen}
        state={globalState}
        onClose={() => setSettingsOpen(false)}
        onChanged={onSettingsChanged}
      />
    </div>
  );
}

function ProjectCard({
  session,
  tags,
  allTags,
  modelPresets,
  onOpen,
  onRename,
  onDelete,
  onApplyTags,
  onCreateTag,
  onRecolorTag,
}: {
  session: SessionInfo;
  tags: Tag[];
  allTags: Tag[];
  modelPresets: ModelPreset[];
  onOpen: () => void;
  onRename: (name: string) => Promise<void>;
  onDelete: () => Promise<void>;
  onApplyTags: (tagIds: string[]) => Promise<void>;
  onCreateTag: (name: string, color: TagColor) => Promise<Tag>;
  onRecolorTag: (tagId: string, color: TagColor) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(session.name ?? "");
  const [saving, setSaving] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [tagAnchor, setTagAnchor] = useState<HTMLElement | null>(null);
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
    if (editing || confirmingDelete || tagAnchor) return;
    onOpen();
  };

  /* Unbound projects reject metadata writes until this device has a path. */
  const tagsLocked = !!session.read_only;

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
        <div
          className={
            "flex flex-none items-center gap-1 transition "
            + (confirmingDelete || tagAnchor
              ? "opacity-100"
              : "opacity-0 group-hover:opacity-100 focus-within:opacity-100")
          }
        >
          {!editing && (
            <button
              type="button"
              disabled={tagsLocked}
              onClick={(e) => {
                e.stopPropagation();
                const trigger = e.currentTarget;
                setTagAnchor((current) => (current ? null : trigger));
              }}
              title={
                tagsLocked
                  ? "此设备尚未配置项目路径，绑定后才能编辑 tag"
                  : "编辑 tag"
              }
              aria-label="编辑 tag"
              aria-expanded={!!tagAnchor}
              className="rounded p-1 text-ink-muted transition hover:bg-surface-sunken hover:text-ink disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-ink-muted"
            >
              <TagIcon />
            </button>
          )}
          {!editing && !session.read_only && (
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
          {session.can_delete && <button
            type="button"
            onClick={handleDelete}
            title={
              confirmingDelete
                ? "Click again to delete this project and its planspaces"
                : "Delete"
            }
            aria-label={
              confirmingDelete
                ? "Confirm delete project and planspaces"
                : "Delete project"
            }
            className={
              "rounded p-1 transition hover:bg-surface-sunken " +
              (confirmingDelete
                ? "text-state-error ring-1 ring-state-error/40"
                : "text-ink-muted hover:text-state-error")
            }
          >
            <TrashIcon />
          </button>}
        </div>
      </div>

      {confirmingDelete && (
        <div className="rounded-md border border-state-error/30 bg-state-error-soft px-2.5 py-2 text-[11px] leading-snug text-state-error">
          再点一次删除项目「{session.name?.trim() || session.id.slice(0, 8)}」。这会移除所有设备上的项目记录与历史，不影响磁盘上的代码仓库。
        </div>
      )}

      <TagChipRow tags={tags} />

      <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
        {!session.bound_here && (
          <span className="rounded border border-state-waiting/40 bg-state-waiting-soft px-1.5 py-0.5 text-state-waiting">
            ○ 需要配置
          </span>
        )}
        {session.preferred_language && (
          <span className="rounded border border-line bg-surface-sunken px-1.5 py-0.5 text-ink-muted">
            {languageLabel(session.preferred_language)}
          </span>
        )}
        <span className="rounded border border-line bg-surface-sunken px-1.5 py-0.5 text-ink-muted">
          {modelPresetLabel(modelPresets, session.model_preset_id)}
        </span>
        {session.template_id && (
          <span className="rounded border border-brand/30 bg-brand-soft px-1.5 py-0.5 text-brand-ink dark:text-brand">
            {session.template_id}
          </span>
        )}
        {session.temporary && (
          <span className="rounded border border-state-waiting/30 bg-state-waiting-soft px-1.5 py-0.5 text-state-waiting">
            temp
          </span>
        )}
      </div>

      <div className="mt-auto flex items-center justify-between text-[11px] text-ink-subtle">
        <span
          title={
            session.last_activity_at
              ? `最近活动 ${new Date(session.last_activity_at * 1000).toLocaleString()}`
              : `创建于 ${new Date(session.created_at * 1000).toLocaleString()}`
          }
        >
          {session.turns} node{session.turns === 1 ? "" : "s"} ·{" "}
          {formatRelative(activityAt(session))}
        </span>
        <span className="font-mono text-ink-subtle">{session.id.slice(0, 8)}</span>
      </div>

      {tagAnchor && (
        <TagEditPopover
          anchor={tagAnchor}
          tags={allTags}
          selectedIds={session.tag_ids ?? []}
          onClose={() => setTagAnchor(null)}
          onApply={onApplyTags}
          onCreateTag={onCreateTag}
          onRecolorTag={onRecolorTag}
        />
      )}
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

function TagIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M1 7.775V2.75C1 1.784 1.784 1 2.75 1h5.025c.464 0 .91.184 1.238.513l6.25 6.25a1.75 1.75 0 0 1 0 2.474l-5.026 5.026a1.75 1.75 0 0 1-2.474 0l-6.25-6.25A1.75 1.75 0 0 1 1 7.775Zm1.5 0c0 .066.026.13.073.177l6.25 6.25a.25.25 0 0 0 .354 0l5.025-5.025a.25.25 0 0 0 0-.354l-6.25-6.25a.25.25 0 0 0-.177-.073H2.75a.25.25 0 0 0-.25.25ZM6 5a1 1 0 1 1 0 2 1 1 0 0 1 0-2Z" />
    </svg>
  );
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
