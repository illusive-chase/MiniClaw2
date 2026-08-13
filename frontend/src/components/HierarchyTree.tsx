import { useMemo } from "react";
import { buildHierarchy, searchEntries, type HierarchyNode } from "../hierarchy";

/** Everything the tree needs to render one selectable entry. */
export type HierarchyEntry = {
  /** Full id used by the caller's data model (e.g. `skills.lark-vc`). */
  id: string;
  /** Hyphen-segmented name the hierarchy is derived from (e.g. `lark-vc`). */
  name: string;
  description?: string | null;
  /** Already attached upstream; rendered dimmed and not selectable. */
  disabled?: boolean;
};

export type HierarchyTreeProps = {
  entries: HierarchyEntry[];
  /** Trimmed query. Non-empty replaces the tree with a flat hit list. */
  query?: string;
  /** Expanded directory paths, joined with `-`. Owned by the caller. */
  expanded: ReadonlySet<string>;
  onToggle: (path: string) => void;
  /** Picking an entry. Omit it where rows are not click targets — the library
   * dock's rows are drag sources with explicit action buttons instead (design
   * §3.2), so their labels render as plain text rather than dead buttons. */
  onSelect?: (entry: HierarchyEntry) => void;
  /** Rendered in place of the tree when `entries` is empty. */
  emptyLabel?: string;
  /** Row suffix, e.g. LibraryDock's preview and delete buttons. */
  renderRowActions?: (entry: HierarchyEntry) => React.ReactNode;
  /** Always-visible marks beside the name, e.g. `new` and `attached N`. */
  renderRowBadges?: (entry: HierarchyEntry) => React.ReactNode;
  /** Extra attributes for the row container, e.g. `draggable`/`onDragStart`.
   * `className` is appended to the row's own classes rather than replacing
   * them. Applied to entry rows only — dragging a directory has no meaning. */
  rowProps?: (
    entry: HierarchyEntry,
  ) => React.HTMLAttributes<HTMLDivElement> & { draggable?: boolean };
};

/* One indent step. Deep enough to read as a level at 11–12px type, shallow
 * enough that four segments (`lark-workflow-meeting-summary`) still leave room
 * for the description in a 380px panel. */
const INDENT_PX = 14;

export function pathKey(fullPath: string[]): string {
  return fullPath.join("-");
}

function Description({ text, lines }: { text: string; lines: 1 | 2 }) {
  /* One line while browsing the tree: 29 entries with two-line Chinese
   * descriptions each buries the structure the tree exists to show. Search hits
   * get two lines because there are only a handful and the description is the
   * main way to tell `lark-vc` from `lark-vc-agent` (design §2.2). */
  return (
    <div
      className={
        "mt-0.5 text-[10.5px] leading-snug text-ink-muted " +
        (lines === 1 ? "truncate" : "line-clamp-2")
      }
    >
      {text}
    </div>
  );
}

/** Subtree entry count. Deliberately quiet — an orientation aid, not data. */
function LeafCount({ count }: { count: number }) {
  return (
    <span className="shrink-0 font-mono text-[9.5px] tabular-nums text-ink-subtle">
      {count}
    </span>
  );
}

function Caret({ open }: { open: boolean }) {
  return (
    <span
      aria-hidden
      className={
        "inline-block w-2.5 shrink-0 text-[9px] leading-none text-ink-muted transition-transform " +
        (open ? "rotate-90" : "")
      }
    >
      ▶
    </span>
  );
}

function EntryRow({
  entry,
  depth,
  label,
  onSelect,
  renderRowActions,
  renderRowBadges,
  rowProps,
  selfMarker,
  descriptionLines,
}: {
  entry: HierarchyEntry;
  depth: number;
  label: React.ReactNode;
  onSelect?: (entry: HierarchyEntry) => void;
  renderRowActions?: (entry: HierarchyEntry) => React.ReactNode;
  renderRowBadges?: (entry: HierarchyEntry) => React.ReactNode;
  rowProps?: (
    entry: HierarchyEntry,
  ) => React.HTMLAttributes<HTMLDivElement> & { draggable?: boolean };
  /* Set on a node that is both a directory and an entry (`lark-vc`), so the
   * first child row is not mistaken for a sibling of the ones below it. */
  selfMarker?: boolean;
  descriptionLines: 1 | 2;
}) {
  const disabled = entry.disabled === true;
  const { className: extraClassName, ...extraProps } = rowProps?.(entry) ?? {};

  const content = (
    <span className="min-w-0 flex-1">
      <span className="flex items-baseline gap-1.5">
        <span className="min-w-0 truncate text-[11.5px] text-ink-strong">{label}</span>
        {selfMarker && (
          <span className="shrink-0 text-[9px] text-ink-subtle" title="该目录自身也是一个条目">
            自身
          </span>
        )}
        {disabled && <span className="shrink-0 text-[9px] text-ink-subtle">已附加</span>}
        {renderRowBadges?.(entry)}
      </span>
      {entry.description ? (
        <Description text={entry.description} lines={descriptionLines} />
      ) : null}
    </span>
  );

  return (
    <div
      {...extraProps}
      className={
        "group flex items-start gap-1.5 rounded-md px-1.5 py-1 hover:bg-surface-sunken "
        + (extraClassName ?? "")
      }
      style={{ paddingLeft: 6 + depth * INDENT_PX }}
    >
      {onSelect ? (
        <button
          type="button"
          disabled={disabled}
          onClick={() => onSelect(entry)}
          title={disabled ? `${entry.name} · 已附加` : entry.name}
          className={
            "flex min-w-0 flex-1 rounded text-left transition "
            + (disabled ? "cursor-default opacity-45" : "cursor-pointer")
          }
        >
          {content}
        </button>
      ) : (
        /* No `onSelect`: the row itself is the affordance (a drag source), so a
         * button here would only add a focus stop that does nothing. */
        <span
          title={entry.name}
          className={"flex min-w-0 flex-1 " + (disabled ? "opacity-45" : "")}
        >
          {content}
        </span>
      )}
      {renderRowActions ? (
        <span className="shrink-0 opacity-0 transition focus-within:opacity-100 group-hover:opacity-100">
          {renderRowActions(entry)}
        </span>
      ) : null}
    </div>
  );
}

function TreeLevel({
  nodes,
  depth,
  entriesByName,
  expanded,
  onToggle,
  onSelect,
  renderRowActions,
  renderRowBadges,
  rowProps,
}: {
  nodes: HierarchyNode[];
  depth: number;
  entriesByName: Map<string, HierarchyEntry>;
  expanded: ReadonlySet<string>;
  onToggle: (path: string) => void;
  onSelect?: (entry: HierarchyEntry) => void;
  renderRowActions?: (entry: HierarchyEntry) => React.ReactNode;
  renderRowBadges?: (entry: HierarchyEntry) => React.ReactNode;
  rowProps?: (
    entry: HierarchyEntry,
  ) => React.HTMLAttributes<HTMLDivElement> & { draggable?: boolean };
}) {
  return (
    <>
      {nodes.map((node) => {
        const key = pathKey(node.fullPath);
        const selfEntry = node.entry === null ? undefined : entriesByName.get(node.entry);

        /* A leaf: render the entry alone, no caret, no count. */
        if (node.children.length === 0) {
          if (!selfEntry) return null;
          return (
            <EntryRow
              key={key}
              entry={selfEntry}
              depth={depth}
              label={node.segment}
              onSelect={onSelect}
              renderRowActions={renderRowActions}
              renderRowBadges={renderRowBadges}
              rowProps={rowProps}
              descriptionLines={1}
            />
          );
        }

        const open = expanded.has(key);
        return (
          <div key={key}>
            <button
              type="button"
              onClick={() => onToggle(key)}
              aria-expanded={open}
              className="flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left hover:bg-surface-sunken"
              style={{ paddingLeft: 6 + depth * INDENT_PX }}
            >
              <Caret open={open} />
              {/* Directories read as structure, not as things you can pick:
                * medium weight and muted ink, against the entry rows' plain
                * strong ink. */}
              <span className="min-w-0 flex-1 truncate text-[11.5px] font-medium text-ink">
                {node.segment}
              </span>
              <LeafCount count={node.leafCount} />
            </button>
            {open && (
              <>
                {/* The directory's own entry leads its children (`lark-vc`
                  * above `lark-vc-agent`), per design §2.1. */}
                {selfEntry && (
                  <EntryRow
                    entry={selfEntry}
                    depth={depth + 1}
                    label={selfEntry.name}
                    onSelect={onSelect}
                    renderRowActions={renderRowActions}
                    renderRowBadges={renderRowBadges}
                    rowProps={rowProps}
                    selfMarker
                    descriptionLines={1}
                  />
                )}
                <TreeLevel
                  nodes={node.children}
                  depth={depth + 1}
                  entriesByName={entriesByName}
                  expanded={expanded}
                  onToggle={onToggle}
                  onSelect={onSelect}
                  renderRowActions={renderRowActions}
                  renderRowBadges={renderRowBadges}
                  rowProps={rowProps}
                />
              </>
            )}
          </div>
        );
      })}
    </>
  );
}

/** Full path as a breadcrumb, with the matched span emphasized. */
function HitLabel({
  fullPath,
  matchRange,
}: {
  fullPath: string[];
  matchRange: [number, number];
}) {
  const [start, end] = matchRange;
  let offset = 0;
  return (
    <span className="min-w-0 truncate">
      {fullPath.map((segment, index) => {
        const segmentStart = offset;
        offset += segment.length + 1;
        const from = Math.max(0, Math.min(segment.length, start - segmentStart));
        const to = Math.max(0, Math.min(segment.length, end - segmentStart));
        return (
          <span key={index}>
            {index > 0 && <span className="mx-1 text-ink-subtle">›</span>}
            {from < to ? (
              <>
                {segment.slice(0, from)}
                <mark className="bg-brand/25 text-ink-strong">{segment.slice(from, to)}</mark>
                {segment.slice(to)}
              </>
            ) : (
              segment
            )}
          </span>
        );
      })}
    </span>
  );
}

export function HierarchyTree({
  entries,
  query = "",
  expanded,
  onToggle,
  onSelect,
  emptyLabel = "暂无条目。",
  renderRowActions,
  renderRowBadges,
  rowProps,
}: HierarchyTreeProps) {
  const entriesByName = useMemo(
    () => new Map(entries.map((entry) => [entry.name, entry])),
    [entries],
  );
  const names = useMemo(() => entries.map((entry) => entry.name), [entries]);
  const trimmedQuery = query.trim();
  const roots = useMemo(
    () => (trimmedQuery.length > 0 ? [] : buildHierarchy(names)),
    [names, trimmedQuery],
  );
  const hits = useMemo(
    () => (trimmedQuery.length > 0 ? searchEntries(names, trimmedQuery) : []),
    [names, trimmedQuery],
  );

  if (entries.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-line bg-surface px-3 py-2.5 text-[11px] text-ink-muted">
        {emptyLabel}
      </div>
    );
  }

  if (trimmedQuery.length > 0) {
    if (hits.length === 0) {
      return (
        <div className="rounded-md border border-dashed border-line bg-surface px-3 py-2.5 text-[11px] text-ink-muted">
          没有名称匹配「{trimmedQuery}」的条目。
        </div>
      );
    }
    return (
      <div className="flex flex-col">
        {hits.map((hit) => {
          const entry = entriesByName.get(hit.entry);
          if (!entry) return null;
          return (
            <EntryRow
              key={hit.entry}
              entry={entry}
              depth={0}
              label={<HitLabel fullPath={hit.fullPath} matchRange={hit.matchRange} />}
              onSelect={onSelect}
              renderRowActions={renderRowActions}
              renderRowBadges={renderRowBadges}
              rowProps={rowProps}
              descriptionLines={2}
            />
          );
        })}
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      <TreeLevel
        nodes={roots}
        depth={0}
        entriesByName={entriesByName}
        expanded={expanded}
        onToggle={onToggle}
        onSelect={onSelect}
        renderRowActions={renderRowActions}
        renderRowBadges={renderRowBadges}
        rowProps={rowProps}
      />
    </div>
  );
}
