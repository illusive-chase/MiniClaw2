/* The code-reading page's file tree.
 *
 * Built from the flat Git-visible list. Only expanded folders are turned
 * into rows; past 2,000 rows they are grouped into 200-row blocks under
 * `content-visibility: auto`, so a huge expanded tree costs layout only
 * for what is on screen.
 */

import { File, FileCode, FileText, Folder, FolderOpen, ChevronRight } from "lucide-react";
import { memo, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import type { CodeTreeEntry } from "../../types";

type DirNode = { kind: "dir"; name: string; path: string; children: TreeNode[]; opaque: boolean };
type FileNode = { kind: "file"; name: string; path: string };
type TreeNode = DirNode | FileNode;

type Row = { node: TreeNode; depth: number };

const ROW_HEIGHT = 24;
const BLOCK = 200;
const BLOCK_AFTER = 2000;
const CODE_EXT = /\.(py|pyi|ts|tsx|js|jsx|mjs|cjs|rs|go|java|kt|c|h|cpp|hpp|cs|rb|php|swift|sh|bash|zsh|css|scss|html|json|ya?ml|toml|sql|lua)$/i;
const TEXT_EXT = /\.(md|markdown|txt|rst)$/i;

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

export function buildTree(entries: CodeTreeEntry[]): DirNode {
  const root: DirNode = { kind: "dir", name: "", path: "", children: [], opaque: false };
  const dirs = new Map<string, DirNode>([["", root]]);
  const ensureDir = (path: string): DirNode => {
    const found = dirs.get(path);
    if (found) return found;
    const slash = path.lastIndexOf("/");
    const parent = ensureDir(slash < 0 ? "" : path.slice(0, slash));
    const dir: DirNode = { kind: "dir", name: path.slice(slash + 1), path, children: [], opaque: false };
    parent.children.push(dir);
    dirs.set(path, dir);
    return dir;
  };
  for (const entry of entries) {
    if (entry.directory) {
      ensureDir(entry.path).opaque = true;
      continue;
    }
    const slash = entry.path.lastIndexOf("/");
    const parent = ensureDir(slash < 0 ? "" : entry.path.slice(0, slash));
    parent.children.push({ kind: "file", name: entry.path.slice(slash + 1), path: entry.path });
  }
  const sort = (dir: DirNode) => {
    dir.children.sort((a, b) =>
      a.kind !== b.kind ? (a.kind === "dir" ? -1 : 1) : collator.compare(a.name, b.name));
    for (const child of dir.children) if (child.kind === "dir") sort(child);
  };
  sort(root);
  return root;
}

function flatten(root: DirNode, expanded: Set<string>): Row[] {
  const rows: Row[] = [];
  const visit = (dir: DirNode, depth: number) => {
    for (const child of dir.children) {
      rows.push({ node: child, depth });
      if (child.kind === "dir" && !child.opaque && expanded.has(child.path)) visit(child, depth + 1);
    }
  };
  visit(root, 0);
  return rows;
}

export function ancestorsOf(path: string): string[] {
  const parts = path.split("/");
  return parts.slice(0, -1).map((_part, index) => parts.slice(0, index + 1).join("/"));
}

function FileIcon({ name }: { name: string }) {
  const Icon = CODE_EXT.test(name) ? FileCode : TEXT_EXT.test(name) ? FileText : File;
  return <Icon size={14} strokeWidth={1.7} className="shrink-0 text-ink-muted" aria-hidden="true" />;
}

type RowProps = {
  row: Row;
  active: boolean;
  focused: boolean;
  open: boolean;
  onActivate: (row: Row, pin: boolean) => void;
  onFocusRow: (path: string) => void;
};

const TreeRow = memo(function TreeRow({ row, active, focused, open, onActivate, onFocusRow }: RowProps) {
  const { node, depth } = row;
  const dir = node.kind === "dir";
  return (
    <div
      role="treeitem"
      aria-level={depth + 1}
      aria-expanded={dir && !node.opaque ? open : undefined}
      aria-selected={active}
      data-path={node.path}
      tabIndex={focused ? 0 : -1}
      onClick={() => {
        onFocusRow(node.path);
        onActivate(row, false);
      }}
      onDoubleClick={() => !dir && onActivate(row, true)}
      title={node.path}
      style={{ paddingLeft: 8 + depth * 14, height: ROW_HEIGHT }}
      className={
        "flex cursor-pointer select-none items-center gap-1.5 whitespace-nowrap pr-2 text-[12.5px] outline-none " +
        (active
          ? "bg-brand-soft text-ink-strong"
          : "text-ink hover:bg-surface-raised") +
        " focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-brand/50"
      }
    >
      {dir ? (
        <>
          <ChevronRight
            size={12}
            strokeWidth={2}
            className={"shrink-0 text-ink-subtle transition-transform " + (open && !node.opaque ? "rotate-90" : "")}
            aria-hidden="true"
          />
          {open && !node.opaque
            ? <FolderOpen size={14} strokeWidth={1.7} className="shrink-0 text-ink-muted" aria-hidden="true" />
            : <Folder size={14} strokeWidth={1.7} className="shrink-0 text-ink-muted" aria-hidden="true" />}
        </>
      ) : (
        <>
          <span className="w-3 shrink-0" />
          <FileIcon name={node.name} />
        </>
      )}
      <span className="truncate">{node.name}</span>
    </div>
  );
});

export type FileTreeProps = {
  rootName: string;
  entries: CodeTreeEntry[];
  truncated: boolean;
  expanded: Set<string>;
  activePath: string | null;
  onToggle: (path: string, open?: boolean) => void;
  onOpen: (path: string, pin: boolean) => void;
  onRefresh: () => void;
  refreshing: boolean;
};

export function FileTree({
  rootName,
  entries,
  truncated,
  expanded,
  activePath,
  onToggle,
  onOpen,
  onRefresh,
  refreshing,
}: FileTreeProps) {
  const root = useMemo(() => buildTree(entries), [entries]);
  const rows = useMemo(() => flatten(root, expanded), [root, expanded]);
  const [focusPath, setFocusPath] = useState<string | null>(null);
  const container = useRef<HTMLDivElement>(null);

  // Auto reveal: scroll the active file into view once its folders are open.
  useEffect(() => {
    if (!activePath || !container.current) return;
    const element = container.current.querySelector<HTMLElement>(`[data-path="${CSS.escape(activePath)}"]`);
    element?.scrollIntoView({ block: "nearest" });
  }, [activePath, rows]);

  const activate = (row: Row, pin: boolean) => {
    if (row.node.kind === "dir") {
      if (!row.node.opaque) onToggle(row.node.path);
      return;
    }
    onOpen(row.node.path, pin);
  };

  const focusIndex = Math.max(0, rows.findIndex((row) => row.node.path === (focusPath ?? activePath)));

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!rows.length) return;
    const row = rows[focusIndex];
    const move = (index: number) => {
      const next = rows[Math.max(0, Math.min(rows.length - 1, index))];
      setFocusPath(next.node.path);
      requestAnimationFrame(() =>
        container.current?.querySelector<HTMLElement>(`[data-path="${CSS.escape(next.node.path)}"]`)?.focus());
    };
    switch (event.key) {
      case "ArrowDown":
        move(focusIndex + 1);
        break;
      case "ArrowUp":
        move(focusIndex - 1);
        break;
      case "ArrowRight":
        if (row.node.kind === "dir" && !row.node.opaque) {
          if (!expanded.has(row.node.path)) onToggle(row.node.path, true);
          else move(focusIndex + 1);
        }
        break;
      case "ArrowLeft":
        if (row.node.kind === "dir" && expanded.has(row.node.path)) onToggle(row.node.path, false);
        else {
          const parent = ancestorsOf(row.node.path).pop();
          const parentIndex = parent ? rows.findIndex((r) => r.node.path === parent) : -1;
          if (parentIndex >= 0) move(parentIndex);
        }
        break;
      case "Enter":
        activate(row, true);
        break;
      case " ":
        activate(row, false);
        break;
      default:
        return;
    }
    event.preventDefault();
  };

  const rowElements = rows.map((row, index) => (
    <TreeRow
      key={row.node.path}
      row={row}
      active={row.node.path === activePath}
      focused={index === focusIndex}
      open={row.node.kind === "dir" && expanded.has(row.node.path)}
      onActivate={activate}
      onFocusRow={setFocusPath}
    />
  ));
  const blocks: JSX.Element[] = [];
  if (rows.length > BLOCK_AFTER) {
    for (let start = 0; start < rowElements.length; start += BLOCK) {
      const slice = rowElements.slice(start, start + BLOCK);
      blocks.push(
        <div
          key={`block-${start}`}
          role="group"
          style={{
            contentVisibility: "auto",
            containIntrinsicSize: `auto ${slice.length * ROW_HEIGHT}px`,
          } as React.CSSProperties}
        >
          {slice}
        </div>,
      );
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-9 shrink-0 items-center justify-between border-b border-line px-3">
        <span className="truncate text-[11px] font-semibold uppercase tracking-wide text-ink-muted" title={rootName}>
          {rootName}
        </span>
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className="inline-flex h-6 w-6 items-center justify-center rounded text-ink-muted transition hover:bg-surface-raised hover:text-ink disabled:opacity-40"
          title="刷新文件树"
          aria-label="刷新文件树"
        >
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={refreshing ? "animate-spin" : ""}>
            <path d="M21 12a9 9 0 1 1-2.64-6.36" />
            <path d="M21 3v6h-6" />
          </svg>
        </button>
      </div>
      {truncated && (
        <div className="shrink-0 border-b border-line bg-state-waiting-soft px-3 py-1.5 text-[11px] text-state-waiting">
          文件过多，仅显示前 20,000 个
        </div>
      )}
      <div
        ref={container}
        role="tree"
        aria-label="项目文件"
        onKeyDown={onKeyDown}
        className="min-h-0 flex-1 overflow-auto py-1"
      >
        {blocks.length ? blocks : rowElements}
        {!rows.length && <div className="px-3 py-2 text-[12px] text-ink-muted">没有可显示的文件</div>}
      </div>
    </div>
  );
}
