import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
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
import { modelPresetLabel } from "../modelPresets";

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
  onError?: (message: string) => void;
  onClose: () => void;
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
  onError,
  onClose,
}: Props) {
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [openSections, setOpenSections] = useState({
    templates: true,
    principles: false,
    skills: false,
  });
  const [expandedEntry, setExpandedEntry] = useState<string | null>(null);
  const [newEntryIds, setNewEntryIds] = useState<Set<string>>(new Set());
  const handledSurfaceTokenRef = useRef(0);

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

  useEffect(() => {
    if (surfaceNewToken <= 0) return;
    setOpenSections((current) => ({ ...current, principles: true, skills: true }));
  }, [surfaceNewToken]);

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
    const first = [...added][0];
    setExpandedEntry(first);
    setOpenSections((state) => ({
      ...state,
      principles: state.principles || first.startsWith("principles."),
      skills: state.skills || first.startsWith("skills."),
    }));
    handledSurfaceTokenRef.current = surfaceNewToken;
  }, [principles, skills, surfaceBaselineIds, surfaceNewToken]);

  const removeTemplate = useCallback(async (slug: string) => {
    try {
      await deleteUserTemplate(slug);
      setTemplates((items) => items.filter((item) => slugFor(item.name) !== slug));
    } catch (err) {
      onError?.(errorMessage(err));
    }
  }, [onError]);

  const attachedCounts = countBindings(nodes, contextBundlesByNodeId);
  const toggleSection = (section: keyof typeof openSections) => {
    setOpenSections((state) => ({ ...state, [section]: !state[section] }));
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

      <div className="flex-1 overflow-y-auto">
        <LibrarySection
          title="Templates"
          count={templates.length}
          open={openSections.templates}
          onToggle={() => toggleSection("templates")}
        >
          {loading && templates.length === 0 && <Empty>Loading...</Empty>}
          {!loading && templates.length === 0 && <Empty>No saved templates.</Empty>}
          {templates.map((template) => {
            const slug = slugFor(template.name);
            return (
              <div
                key={slug}
                draggable
                onDragStart={(event) => setDragData(event, "application/x-miniclaw-template", slug)}
                className="group cursor-grab rounded-md border border-line bg-surface-raised px-3 py-2 text-xs shadow-card transition hover:border-brand/60 active:cursor-grabbing"
                title="Drag onto the canvas to apply this template"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium text-ink-strong">{template.name}</div>
                    {template.brief && <div className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-ink-muted">{template.brief}</div>}
                    <div className="mt-1 text-[10px] text-ink-subtle">
                      {template.node_count} {template.node_count === 1 ? "node" : "nodes"}
                      {template.allowed_model_preset_ids.length > 0
                        ? ` · ${template.allowed_model_preset_ids.map((id) => modelPresetLabel(modelPresets, id)).join(", ")}`
                        : ""}
                    </div>
                  </div>
                  <DeleteButton label={`Delete template ${template.name}`} onDelete={() => removeTemplate(slug)} />
                </div>
              </div>
            );
          })}
        </LibrarySection>

        <LibrarySection
          title="Principles"
          count={principles.length}
          open={openSections.principles}
          onToggle={() => toggleSection("principles")}
        >
          {principles.length === 0 && <Empty>No principles.</Empty>}
          {principles.map((principle) => {
            const path = `${principle.path}/CONTEXT.md`;
            return (
              <EntryCard
                key={principle.id}
                id={principle.id}
                title={principle.title}
                description={principle.description}
                kind="principle"
                mime="application/x-miniclaw-principle"
                expanded={expandedEntry === principle.id}
                isNew={newEntryIds.has(principle.id)}
                attachedCount={attachedCounts[principle.id] ?? 0}
                onToggle={() => setExpandedEntry((id) => id === principle.id ? null : principle.id)}
              >
                <div className="break-all font-mono text-[10px] text-ink-subtle">{path}</div>
                <div className="text-[10.5px] text-ink-muted">Injection: {formatValue(principle.injection)} · limit: {formatValue(principle.max_chars)}</div>
                <EntryActions
                  onOpen={() => onOpenFull({ identityKey: contextIdentityKey("principle", path), path, sourceKind: "principle", plugId: principle.id })}
                  onDelete={() => onDeletePrinciple(principle.slug)}
                  deleteLabel={`Delete principle ${principle.title}`}
                />
              </EntryCard>
            );
          })}
        </LibrarySection>

        <LibrarySection
          title="Skills"
          count={skills.length}
          open={openSections.skills}
          onToggle={() => toggleSection("skills")}
        >
          {skills.length === 0 && <Empty>No skills.</Empty>}
          {skills.map((skill) => {
            const path = `${skill.path}/SKILL.md`;
            return (
              <EntryCard
                key={skill.id}
                id={skill.id}
                title={skill.title}
                description={skill.description}
                kind="skill"
                mime="application/x-miniclaw-skill"
                expanded={expandedEntry === skill.id}
                isNew={newEntryIds.has(skill.id)}
                attachedCount={attachedCounts[skill.id] ?? 0}
                onToggle={() => setExpandedEntry((id) => id === skill.id ? null : skill.id)}
              >
                <div className="break-all font-mono text-[10px] text-ink-subtle">{path}</div>
                <div className="text-[10.5px] text-ink-muted">
                  {skill.files.length} files{skill.version ? ` · v${skill.version}` : ""}{skill.import_source ? ` · ${skill.import_source}` : ""}
                </div>
                {skill.files.length > 0 && <div className="max-h-24 overflow-auto font-mono text-[10px] text-ink-muted">{skill.files.join("\n")}</div>}
                <EntryActions
                  onOpen={() => onOpenFull({ identityKey: contextIdentityKey("skill", path), path, sourceKind: "skill", plugId: skill.id })}
                  onDelete={() => onDeleteSkill(skill.slug)}
                  deleteLabel={`Delete skill ${skill.title}`}
                />
              </EntryCard>
            );
          })}
        </LibrarySection>
      </div>
    </div>
  );
}

function LibrarySection({ title, count, open, onToggle, children }: { title: string; count: number; open: boolean; onToggle: () => void; children: ReactNode }) {
  return (
    <section className="border-b border-line">
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full items-center justify-between px-3 py-2.5 text-left transition hover:bg-surface-raised">
        <span className="text-[11px] font-medium text-ink-strong">{title}</span>
        <span className="flex items-center gap-2 font-mono text-[10px] text-ink-subtle"><span>{count}</span><span aria-hidden="true">{open ? "▾" : "▸"}</span></span>
      </button>
      {open && <div className="space-y-2 px-3 pb-3">{children}</div>}
    </section>
  );
}

function EntryCard({ id, title, description, kind, mime, expanded, isNew, attachedCount, onToggle, children }: { id: string; title: string; description: string | null; kind: "principle" | "skill"; mime: string; expanded: boolean; isNew: boolean; attachedCount: number; onToggle: () => void; children: ReactNode }) {
  return (
    <div draggable onDragStart={(event) => setDragData(event, mime, id)} className="cursor-grab rounded-md border border-line bg-surface-raised px-3 py-2 shadow-card transition hover:border-brand/60 active:cursor-grabbing" title={`Drag onto a virtual node to attach this ${kind}`}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-xs font-medium text-ink-strong">{title}</span>
            {isNew && <span className="rounded border border-brand/40 bg-brand-soft px-1 py-0.5 text-[8px] font-medium uppercase text-brand">new</span>}
          </div>
          {description && <div className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-ink-muted">{description}</div>}
          <div className="mt-1 font-mono text-[9.5px] text-ink-subtle">attached {attachedCount}</div>
        </div>
        <button type="button" onClick={(event) => { event.stopPropagation(); onToggle(); }} onMouseDown={(event) => event.stopPropagation()} className="nodrag flex h-6 w-6 items-center justify-center rounded text-[11px] text-ink-muted hover:bg-surface-sunken" title={expanded ? "Collapse details" : "Expand details"} aria-label={expanded ? "Collapse details" : "Expand details"}>{expanded ? "▾" : "▸"}</button>
      </div>
      {expanded && <div className="nodrag mt-2 space-y-2 border-t border-line pt-2" onMouseDown={(event) => event.stopPropagation()}>{children}</div>}
    </div>
  );
}

function EntryActions({ onOpen, onDelete, deleteLabel }: { onOpen: () => void; onDelete: () => Promise<void> | void; deleteLabel: string }) {
  return <div className="flex items-center gap-2"><button type="button" onClick={onOpen} className="rounded border border-line px-2 py-1 text-[10.5px] text-ink-muted hover:border-line-strong hover:text-ink">Open full</button><DeleteButton label={deleteLabel} onDelete={onDelete} visible /></div>;
}

function DeleteButton({ label, onDelete, visible = false }: { label: string; onDelete: () => Promise<void> | void; visible?: boolean }) {
  return <button type="button" onClick={(event) => { event.stopPropagation(); if (window.confirm(`${label}?`)) void onDelete(); }} className={`rounded px-1.5 py-1 text-[10px] text-ink-subtle transition hover:bg-state-error-soft hover:text-state-error ${visible ? "" : "opacity-0 group-hover:opacity-100"}`} title={label} aria-label={label}>×</button>;
}

function IconButton({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return <button type="button" onClick={onClick} className="flex h-6 w-6 items-center justify-center rounded text-[12px] text-ink-muted transition hover:bg-surface-raised hover:text-ink" title={label} aria-label={label}>{children}</button>;
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="py-1 text-[11px] text-ink-subtle">{children}</div>;
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

function slugFor(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "default";
  return typeof value === "string" || typeof value === "number" ? String(value) : JSON.stringify(value);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
