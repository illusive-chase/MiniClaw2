import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  deleteUserTemplate,
  listUserTemplates,
  type PrincipleSummary,
  type SkillSummary,
} from "../api";
import type {
  ContextBundle,
  ModelPreset,
  NodeInfo,
  TemplateSummary,
} from "../types";
import { ancestorDirectoryPaths, searchEntries } from "../hierarchy";
import { HierarchyTree, type HierarchyEntry } from "./HierarchyTree";
import {
  readLibraryTreeState,
  writeLibraryTreeState,
  type LibrarySectionKey,
  type LibraryTreeState,
} from "../libraryTreeState";
import {
  LibraryEntryPreviewModal,
  type LibraryPreviewTarget,
} from "./LibraryEntryPreviewModal";

export type LibraryEntrySelection = {
  identityKey: string;
  path: string;
  sourceKind: "principle" | "skill";
  plugId: string;
};

type Props = {
  refreshToken: number;
  surfaceNewToken: number;
  surfaceBaselineIds: string[];
  modelPresets: ModelPreset[];
  principles: PrincipleSummary[];
  skills: SkillSummary[];
  nodes: NodeInfo[];
  contextBundlesByNodeId: Record<string, ContextBundle | null>;
  onRefreshEntries: () => Promise<unknown> | void;
  onDeletePrinciple: (slug: string) => Promise<void> | void;
  onDeleteSkill: (slug: string) => Promise<void> | void;
  onOpenFull: (entry: LibraryEntrySelection) => void;
  /** Opens the template editor on this slug. */
  onEditTemplate: (slug: string) => void;
  /** Stamps a template onto the canvas — the preview modal's primary action,
   * sharing the drag-drop path's anchor logic. */
  onApplyTemplate?: (slug: string) => void;
  /** Attaches a skill/principle id to the selected virtual node. Omitted when
   * the current selection cannot receive one. */
  onAttachToVirtual?: (entryId: string) => void;
  /** Label of the node `onAttachToVirtual` would target. */
  attachTargetLabel?: string | null;
  onError?: (message: string) => void;
  onClose: () => void;
};

const MIME_BY_SECTION: Record<LibrarySectionKey, string> = {
  templates: "application/x-miniclaw-template",
  principles: "application/x-miniclaw-principle",
  skills: "application/x-miniclaw-skill",
};

export function LibraryDock({
  refreshToken,
  surfaceNewToken,
  surfaceBaselineIds,
  modelPresets,
  principles,
  skills,
  nodes,
  contextBundlesByNodeId,
  onRefreshEntries,
  onDeletePrinciple,
  onDeleteSkill,
  onOpenFull,
  onEditTemplate,
  onApplyTemplate,
  onAttachToVirtual,
  attachTargetLabel,
  onError,
  onClose,
}: Props) {
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  /* Section open state and expanded directories restored from localStorage.
   * Before the tree, a reset cost nothing; now it costs reopening `lark ›
   * workflow` on every mount (design §3.1). */
  const [treeState, setTreeState] = useState<LibraryTreeState>(() =>
    readLibraryTreeState(),
  );
  const [newEntryIds, setNewEntryIds] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<LibraryPreviewTarget | null>(null);
  const handledSurfaceTokenRef = useRef(0);

  useEffect(() => {
    writeLibraryTreeState(treeState);
  }, [treeState]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [items] = await Promise.all([
        listUserTemplates(),
        Promise.resolve(onRefreshEntries()),
      ]);
      setTemplates(items);
    } catch (err) {
      onError?.(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [onError, onRefreshEntries]);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshToken]);

  const setSectionOpen = useCallback((section: LibrarySectionKey, open: boolean) => {
    setTreeState((current) =>
      current.open[section] === open
        ? current
        : { ...current, open: { ...current.open, [section]: open } },
    );
  }, []);

  const toggleSection = useCallback((section: LibrarySectionKey) => {
    setTreeState((current) => ({
      ...current,
      open: { ...current.open, [section]: !current.open[section] },
    }));
  }, []);

  const toggleDirectory = useCallback((section: LibrarySectionKey, path: string) => {
    setTreeState((current) => {
      const paths = current.expanded[section];
      const next = paths.includes(path)
        ? paths.filter((entry) => entry !== path)
        : [...paths, path];
      return { ...current, expanded: { ...current.expanded, [section]: next } };
    });
  }, []);

  useEffect(() => {
    if (surfaceNewToken <= 0) return;
    setSectionOpen("principles", true);
    setSectionOpen("skills", true);
  }, [setSectionOpen, surfaceNewToken]);

  /* A freshly created principle/skill gets the `new` badge and is revealed:
   * its section opens and the directories above it expand, since a new entry
   * buried inside a collapsed `lark` would otherwise be invisible. */
  useEffect(() => {
    if (
      surfaceNewToken <= 0 ||
      handledSurfaceTokenRef.current === surfaceNewToken
    ) {
      return;
    }
    const baseline = new Set(surfaceBaselineIds);
    const added = new Set([
      ...principles.map((item) => item.id),
      ...skills.map((item) => item.id),
    ].filter((id) => !baseline.has(id)));
    if (added.size === 0) return;
    setNewEntryIds(added);
    handledSurfaceTokenRef.current = surfaceNewToken;

    const first = [...added][0];
    const isSkill = first.startsWith("skills.");
    const section: LibrarySectionKey = isSkill ? "skills" : "principles";
    const slug = first.slice(first.indexOf(".") + 1);
    const names = (isSkill ? skills : principles).map((item) => item.slug);
    const reveal = ancestorDirectoryPaths(names, slug);
    setTreeState((current) => ({
      open: { ...current.open, [section]: true },
      expanded: {
        ...current.expanded,
        [section]: [...new Set([...current.expanded[section], ...reveal])],
      },
    }));
  }, [principles, skills, surfaceBaselineIds, surfaceNewToken]);

  const removeTemplate = useCallback(async (slug: string) => {
    try {
      await deleteUserTemplate(slug);
      setTemplates((items) => items.filter((item) => item.slug !== slug));
    } catch (err) {
      onError?.(errorMessage(err));
    }
  }, [onError]);

  const attachedCounts = useMemo(
    () => countBindings(nodes, contextBundlesByNodeId),
    [nodes, contextBundlesByNodeId],
  );

  /* `slug` drives the hierarchy; `id` is what drags and previews speak. */
  const templateEntries: HierarchyEntry[] = useMemo(
    () =>
      templates.map((template) => ({
        id: template.slug,
        name: template.slug,
        description: template.brief || null,
      })),
    [templates],
  );
  const principleEntries: HierarchyEntry[] = useMemo(
    () =>
      principles.map((principle) => ({
        id: principle.id,
        name: principle.slug,
        description: principle.description,
      })),
    [principles],
  );
  const skillEntries: HierarchyEntry[] = useMemo(
    () =>
      skills.map((skill) => ({
        id: skill.id,
        name: skill.slug,
        description: skill.description,
      })),
    [skills],
  );

  const templatesBySlug = useMemo(
    () => new Map(templates.map((item) => [item.slug, item])),
    [templates],
  );
  const principlesById = useMemo(
    () => new Map(principles.map((item) => [item.id, item])),
    [principles],
  );
  const skillsById = useMemo(
    () => new Map(skills.map((item) => [item.id, item])),
    [skills],
  );

  const trimmedQuery = query.trim();

  /* Hit counts drive both the per-section badge and the auto-collapse: with a
   * query active, a section that matches nothing folds itself away so the eye
   * lands on the section that did match (design §3.1). */
  const hitCounts = useMemo(() => {
    if (trimmedQuery.length === 0) return null;
    const count = (entries: HierarchyEntry[]) =>
      searchEntries(entries.map((entry) => entry.name), trimmedQuery).length;
    return {
      templates: count(templateEntries),
      principles: count(principleEntries),
      skills: count(skillEntries),
    };
  }, [principleEntries, skillEntries, templateEntries, trimmedQuery]);

  const sections: Array<{
    key: LibrarySectionKey;
    title: string;
    entries: HierarchyEntry[];
    total: number;
    emptyLabel: string;
  }> = [
    {
      key: "templates",
      title: "Templates",
      entries: templateEntries,
      total: templates.length,
      emptyLabel: loading ? "正在加载…" : "还没有保存的模板。框选画布上的节点即可保存一个。",
    },
    {
      key: "principles",
      title: "Principles",
      entries: principleEntries,
      total: principles.length,
      /* §7 called this out: principles is empty on a fresh install, so the
       * empty state has to carry the next step rather than just say "none". */
      emptyLabel: "还没有 principle。在画布上新建节点，把分类切到「Library」即可创建。",
    },
    {
      key: "skills",
      title: "Skills",
      entries: skillEntries,
      total: skills.length,
      emptyLabel: "还没有 skill。可在项目面板导入，或在画布上新建节点后把分类切到「Library」。",
    },
  ];

  const openPreview = (section: LibrarySectionKey, entry: HierarchyEntry) => {
    if (section === "templates") {
      const summary = templatesBySlug.get(entry.id);
      if (summary) setPreview({ kind: "template", slug: entry.id, summary });
      return;
    }
    if (section === "principles") {
      const summary = principlesById.get(entry.id);
      if (summary) setPreview({ kind: "principle", slug: summary.slug, summary });
      return;
    }
    const summary = skillsById.get(entry.id);
    if (summary) setPreview({ kind: "skill", slug: summary.slug, summary });
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-ink-subtle">
          Library
        </div>
        <div className="flex items-center gap-1">
          <IconButton label="Refresh library" onClick={() => void refresh()}>↻</IconButton>
          <IconButton label="Close panel" onClick={onClose}>×</IconButton>
        </div>
      </div>

      {/* One box across all three sections: it answers "I know the name but not
        * whether it is a template or a skill" (design §3.1). */}
      <div className="border-b border-line px-3 py-2">
        <div className="relative">
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索全库…"
            aria-label="按名称搜索整个 library"
            className="w-full rounded-md border border-line bg-surface-sunken px-2.5 py-1.5 text-[11.5px] text-ink-strong placeholder:text-ink-subtle focus:border-brand focus:outline-none"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {sections.map((section) => {
          const hits = hitCounts?.[section.key] ?? null;
          /* Auto-collapse is a display override, not a state write: clearing
           * the query must restore exactly what the user had open. */
          const open = hits === null ? treeState.open[section.key] : hits > 0;
          return (
            <LibrarySection
              key={section.key}
              title={section.title}
              count={hits ?? section.total}
              countHint={hits !== null ? `${hits} 个命中，共 ${section.total} 个` : undefined}
              matching={hits !== null}
              open={open}
              onToggle={() => toggleSection(section.key)}
            >
              <HierarchyTree
                entries={section.entries}
                query={trimmedQuery}
                expanded={new Set(treeState.expanded[section.key])}
                onToggle={(path) => toggleDirectory(section.key, path)}
                emptyLabel={section.emptyLabel}
                rowProps={(entry) => ({
                  draggable: true,
                  onDragStart: (event) =>
                    setDragData(event, MIME_BY_SECTION[section.key], entry.id),
                  className: "cursor-grab active:cursor-grabbing",
                  title:
                    section.key === "templates"
                      ? "拖到画布以应用这个模板"
                      : `拖到虚拟节点以附加这个 ${section.key === "skills" ? "skill" : "principle"}`,
                })}
                renderRowBadges={(entry) => (
                  <RowBadges
                    isNew={newEntryIds.has(entry.id)}
                    attachedCount={
                      section.key === "templates" ? null : attachedCounts[entry.id] ?? 0
                    }
                  />
                )}
                renderRowActions={(entry) => (
                  <RowActions
                    onPreview={() => openPreview(section.key, entry)}
                    onOpenFull={
                      section.key === "templates"
                        ? undefined
                        : () => {
                            const isSkill = section.key === "skills";
                            const summary = isSkill
                              ? skillsById.get(entry.id)
                              : principlesById.get(entry.id);
                            if (!summary) return;
                            const path = `${summary.path}/${isSkill ? "SKILL.md" : "CONTEXT.md"}`;
                            onOpenFull({
                              identityKey: contextIdentityKey(
                                isSkill ? "skill" : "principle",
                                path,
                              ),
                              path,
                              sourceKind: isSkill ? "skill" : "principle",
                              plugId: summary.id,
                            });
                          }
                    }
                    onDelete={() => {
                      if (section.key === "templates") return removeTemplate(entry.id);
                      const slug = entry.name;
                      return section.key === "skills"
                        ? onDeleteSkill(slug)
                        : onDeletePrinciple(slug);
                    }}
                    deleteLabel={`删除 ${entry.name}`}
                  />
                )}
              />
            </LibrarySection>
          );
        })}
      </div>

      <LibraryEntryPreviewModal
        target={preview}
        modelPresets={modelPresets}
        onClose={() => setPreview(null)}
        onApplyTemplate={
          onApplyTemplate
            ? (slug) => {
                setPreview(null);
                onApplyTemplate(slug);
              }
            : undefined
        }
        onEditTemplate={(slug) => {
          setPreview(null);
          onEditTemplate(slug);
        }}
        onAttachToVirtual={
          onAttachToVirtual
            ? (entryId) => {
                setPreview(null);
                onAttachToVirtual(entryId);
              }
            : undefined
        }
        attachTargetLabel={attachTargetLabel}
      />
    </div>
  );
}

function LibrarySection({
  title,
  count,
  countHint,
  matching,
  open,
  onToggle,
  children,
}: {
  title: string;
  count: number;
  countHint?: string;
  /** A query is active, so `count` is a hit count rather than a total. */
  matching: boolean;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className="border-b border-line">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-3 py-2.5 text-left transition hover:bg-surface-raised"
      >
        <span className="text-[11px] font-medium text-ink-strong">{title}</span>
        <span className="flex items-center gap-2 font-mono text-[10px] text-ink-subtle" title={countHint}>
          {/* While searching, a non-zero hit count is the thing worth looking
            * at, so it takes brand ink; a zero stays quiet and the section
            * folds itself. */}
          <span className={matching && count > 0 ? "text-brand" : undefined}>{count}</span>
          <span aria-hidden="true">{open ? "▾" : "▸"}</span>
        </span>
      </button>
      {open && <div className="px-1.5 pb-2">{children}</div>}
    </section>
  );
}

/** Always-visible marks: `new` and the attachment count. Kept from the flat
 * cards, moved onto the leaf row (design §3.1). */
function RowBadges({
  isNew,
  attachedCount,
}: {
  isNew: boolean;
  attachedCount: number | null;
}) {
  return (
    <>
      {isNew && (
        <span className="shrink-0 rounded border border-brand/40 bg-brand-soft px-1 text-[8px] font-medium uppercase text-brand">
          new
        </span>
      )}
      {attachedCount !== null && attachedCount > 0 && (
        /* Tinted pill, not a bare number: the directory rows already end in a
         * plain subtle count, and "attached to 8 nodes" must not read as
         * "contains 8 entries". The full sentence lives in the tooltip. */
        <span
          className="flex shrink-0 items-center gap-0.5 rounded-full bg-state-review/15 px-1.5 text-[9px] font-medium leading-[1.35] text-state-review"
          title={`已被 ${attachedCount} 个节点附加`}
        >
          <svg viewBox="0 0 24 24" width="7" height="7" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden="true">
            <path d="M9.5 14.5 14.5 9.5" />
            <path d="M7 12 4.8 14.2a3.6 3.6 0 0 0 5.1 5.1L12 17" />
            <path d="M17 12l2.2-2.2a3.6 3.6 0 0 0-5.1-5.1L12 7" />
          </svg>
          {attachedCount}
        </span>
      )}
    </>
  );
}

/* Hover-revealed by the row wrapper. Explicit buttons rather than click-to-
 * preview: the row is a drag source, and a jittered drag should not throw a
 * modal over the canvas (design §3.2). */
function RowActions({
  onPreview,
  onOpenFull,
  onDelete,
  deleteLabel,
}: {
  onPreview: () => void;
  onOpenFull?: () => void;
  onDelete: () => Promise<void> | void;
  deleteLabel: string;
}) {
  return (
    <span className="flex items-center gap-0.5">
      <RowIconButton label="预览" onClick={onPreview}>
        {/* An eye, drawn inline: the dock has no icon set to draw from. */}
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M1.8 12S5.4 5.5 12 5.5 22.2 12 22.2 12 18.6 18.5 12 18.5 1.8 12 1.8 12Z" />
          <circle cx="12" cy="12" r="2.6" />
        </svg>
      </RowIconButton>
      {onOpenFull && (
        <RowIconButton label="在侧栏中打开" onClick={onOpenFull}>
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M14 4h6v6" />
            <path d="M20 4l-8 8" />
            <path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
          </svg>
        </RowIconButton>
      )}
      <RowIconButton
        label={deleteLabel}
        danger
        onClick={() => {
          if (window.confirm(`${deleteLabel}?`)) void onDelete();
        }}
      >
        ×
      </RowIconButton>
    </span>
  );
}

function RowIconButton({
  label,
  onClick,
  danger = false,
  children,
}: {
  label: string;
  onClick: () => void;
  danger?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      /* The row above is a drag source: without stopping these, a click on the
       * button would also start a drag gesture on the row. */
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      onMouseDown={(event) => event.stopPropagation()}
      draggable={false}
      onDragStart={(event) => event.preventDefault()}
      className={
        "nodrag flex h-5 w-5 items-center justify-center rounded text-[11px] leading-none transition "
        + (danger
          ? "text-ink-subtle hover:bg-state-error-soft hover:text-state-error"
          : "text-ink-muted hover:bg-surface-raised hover:text-ink")
      }
      title={label}
      aria-label={label}
    >
      {children}
    </button>
  );
}

function IconButton({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return <button type="button" onClick={onClick} className="flex h-6 w-6 items-center justify-center rounded text-[12px] text-ink-muted transition hover:bg-surface-raised hover:text-ink" title={label} aria-label={label}>{children}</button>;
}

function setDragData(event: React.DragEvent, mime: string, value: string): void {
  event.dataTransfer.setData(mime, value);
  event.dataTransfer.effectAllowed = "copy";
}

function countBindings(
  nodes: NodeInfo[],
  contextBundlesByNodeId: Record<string, ContextBundle | null>,
): Record<string, number> {
  const counts: Record<string, number> = {};
  const ownersByEntry = new Map<string, Set<string>>();
  const add = (entryId: string, ownerId: string) => {
    const owners = ownersByEntry.get(entryId) ?? new Set<string>();
    owners.add(ownerId);
    ownersByEntry.set(entryId, owners);
  };
  for (const node of nodes) {
    if (node.state === "virtual") {
      for (const raw of node.pending_extra_principles ?? []) {
        const id = raw.includes(".") ? raw : `principles.${raw}`;
        add(id, node.id);
      }
      for (const selection of node.pending_extra_skills ?? []) {
        add(selection.id, node.id);
      }
    }
    const audit = node.settings_snapshot?.skill_audit;
    if (Array.isArray(audit)) {
      for (const raw of audit) {
        if (!raw || typeof raw !== "object") continue;
        const item = raw as Record<string, unknown>;
        if (item.missing === true || item.failed === true) continue;
        if (typeof item.id === "string") add(item.id, node.id);
      }
    }
  }
  for (const [ownerId, bundle] of Object.entries(contextBundlesByNodeId)) {
    for (const source of bundle?.sources ?? []) {
      if (source.plug_id?.startsWith("principles.") || source.plug_id?.startsWith("skills.")) {
        add(source.plug_id, ownerId);
      }
    }
  }
  for (const [entryId, owners] of ownersByEntry) counts[entryId] = owners.size;
  return counts;
}

function contextIdentityKey(kind: "principle" | "skill", path: string): string {
  return `contextspace::${kind}::${path}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
