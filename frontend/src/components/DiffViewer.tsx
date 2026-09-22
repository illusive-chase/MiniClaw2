import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRightLeft,
  ExternalLink,
  FilePenLine,
  FilePlus2,
  FileX2,
  X,
} from "lucide-react";

import { markdownRouteUrl } from "../markdownRoute";

export type DiffFile = {
  path: string;
  status: "added" | "modified" | "deleted" | "renamed";
  old_path?: string | null;
  old_mode?: string | null;
  new_mode?: string | null;
  additions: number;
  deletions: number;
  binary: boolean;
  inlined: boolean;
  before?: string | null;
  after?: string | null;
  omitted_reason?: string;
};

export type DiffArtifact = {
  kind: "miniclaw2.diff/v1";
  base: { tree: string; at: string };
  head: { tree: string; at: string };
  concurrent_node_ids: string[];
  totals: { files: number; additions: number; deletions: number };
  truncated: boolean;
  omitted_files?: number;
  files: DiffFile[];
};

export function parseDiffArtifact(value: unknown): DiffArtifact | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<DiffArtifact>;
  if (
    candidate.kind !== "miniclaw2.diff/v1" ||
    !candidate.base ||
    !candidate.head ||
    !candidate.totals ||
    !Array.isArray(candidate.files) ||
    !Array.isArray(candidate.concurrent_node_ids)
  ) return null;
  return candidate as DiffArtifact;
}

type AlignedLine = {
  left?: { number?: number; text: string; eofMarker?: boolean };
  right?: { number?: number; text: string; eofMarker?: boolean };
  changed: boolean;
};

export function alignLines(before: string | null, after: string | null): AlignedLine[] {
  const leftText = before ?? "";
  const rightText = after ?? "";
  const leftMissingNewline = before !== null && before.length > 0 && !before.endsWith("\n");
  const rightMissingNewline = after !== null && after.length > 0 && !after.endsWith("\n");
  const left = leftText.split("\n");
  const right = rightText.split("\n");
  if (left.at(-1) === "") left.pop();
  if (right.at(-1) === "") right.pop();
  if (leftText === "") left.length = 0;
  if (rightText === "") right.length = 0;
  if (left.length * right.length > 250_000) {
    const size = Math.max(left.length, right.length);
    const rows = Array.from({ length: size }, (_, index) => ({
      left: index < left.length ? { number: index + 1, text: left[index] } : undefined,
      right: index < right.length ? { number: index + 1, text: right[index] } : undefined,
      changed: left[index] !== right[index],
    }));
    appendEofMarker(rows, before, after, leftMissingNewline, rightMissingNewline);
    return rows;
  }
  const dp = Array.from({ length: left.length + 1 }, () =>
    new Uint32Array(right.length + 1),
  );
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      dp[i][j] = left[i] === right[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const rows: AlignedLine[] = [];
  let i = 0;
  let j = 0;
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) {
      rows.push({
        left: { number: i + 1, text: left[i] },
        right: { number: j + 1, text: right[j] },
        changed: false,
      });
      i += 1;
      j += 1;
    } else if (j < right.length && (i >= left.length || dp[i][j + 1] >= dp[i + 1][j])) {
      rows.push({ right: { number: j + 1, text: right[j] }, changed: true });
      j += 1;
    } else {
      rows.push({ left: { number: i + 1, text: left[i] }, changed: true });
      i += 1;
    }
  }
  appendEofMarker(rows, before, after, leftMissingNewline, rightMissingNewline);
  return rows;
}

function appendEofMarker(
  rows: AlignedLine[],
  before: string | null,
  after: string | null,
  leftMissingNewline: boolean,
  rightMissingNewline: boolean,
) {
  if (before === after || (!leftMissingNewline && !rightMissingNewline)) return;
  const marker = { text: "\\ 文件末尾没有换行符", eofMarker: true };
  rows.push({
    left: leftMissingNewline ? marker : undefined,
    right: rightMissingNewline ? marker : undefined,
    changed: true,
  });
}

function statusIcon(status: DiffFile["status"]) {
  if (status === "added") return <FilePlus2 size={14} />;
  if (status === "deleted") return <FileX2 size={14} />;
  if (status === "renamed") return <ArrowRightLeft size={14} />;
  return <FilePenLine size={14} />;
}

export function DiffViewer({ artifact }: { artifact: DiffArtifact }) {
  const [selectedPath, setSelectedPath] = useState(artifact.files[0]?.path ?? "");
  const [expandedRuns, setExpandedRuns] = useState<Set<number>>(new Set());
  const [mobileSide, setMobileSide] = useState<"before" | "after">("after");
  const selected = artifact.files.find((file) => file.path === selectedPath) ?? artifact.files[0];
  const rows = useMemo(
    () => selected?.inlined
      ? alignLines(selected.before ?? null, selected.after ?? null)
      : [],
    [selected],
  );

  useEffect(() => setExpandedRuns(new Set()), [selected?.path]);

  const visibleRows = useMemo(() => {
    const result: Array<{ row?: AlignedLine; hidden?: number; key: number }> = [];
    let index = 0;
    while (index < rows.length) {
      if (rows[index].changed) {
        result.push({ row: rows[index], key: index });
        index += 1;
        continue;
      }
      let end = index;
      while (end < rows.length && !rows[end].changed) end += 1;
      const count = end - index;
      if (count > 12 && !expandedRuns.has(index)) {
        for (let cursor = index; cursor < index + 4; cursor += 1) {
          result.push({ row: rows[cursor], key: cursor });
        }
        result.push({ hidden: count - 8, key: index + 4 });
        for (let cursor = end - 4; cursor < end; cursor += 1) {
          result.push({ row: rows[cursor], key: cursor });
        }
      } else {
        for (let cursor = index; cursor < end; cursor += 1) {
          result.push({ row: rows[cursor], key: cursor });
        }
      }
      index = end;
    }
    return result;
  }, [rows, expandedRuns]);

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-surface md:flex-row">
      <aside className="w-full flex-none border-b border-line bg-surface-sunken md:w-64 md:border-b-0 md:border-r">
        <div className="border-b border-line px-3 py-3 text-[11px] text-ink-muted">
          <span className="font-medium text-ink-strong">{artifact.totals.files} files</span>
          <span className="ml-2 text-state-review">+{artifact.totals.additions}</span>
          <span className="ml-1 text-state-error">-{artifact.totals.deletions}</span>
          {!!artifact.omitted_files && <span className="ml-2">省略 {artifact.omitted_files}</span>}
        </div>
        <select
          value={selected?.path ?? ""}
          onChange={(event) => setSelectedPath(event.target.value)}
          className="m-3 w-[calc(100%-1.5rem)] rounded border border-line bg-surface-raised px-2 py-1.5 text-xs text-ink md:hidden"
        >
          {artifact.files.map((file) => <option key={file.path} value={file.path}>{file.path}</option>)}
        </select>
        <div className="hidden max-h-full overflow-y-auto p-2 md:block">
          {artifact.files.map((file) => (
            <button
              key={file.path}
              type="button"
              onClick={() => setSelectedPath(file.path)}
              className={`mb-1 flex w-full items-start gap-2 rounded px-2 py-2 text-left text-[11px] transition ${selected?.path === file.path ? "bg-surface-raised text-ink-strong shadow-card" : "text-ink-muted hover:bg-surface-raised"}`}
            >
              <span className="mt-0.5 flex-none">{statusIcon(file.status)}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate" title={file.path}>{file.path}</span>
                <span className="mt-0.5 block font-mono text-[9.5px]">
                  <span className="text-state-review">+{file.additions}</span>{" "}
                  <span className="text-state-error">-{file.deletions}</span>
                  {!file.inlined ? " · 未内联" : ""}
                </span>
              </span>
            </button>
          ))}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {selected ? (
          <>
            <div className="flex min-h-11 items-center justify-between gap-3 border-b border-line bg-surface-raised px-3">
              <div className="flex min-w-0 items-center gap-2 font-mono text-[11px] text-ink-strong">
                <span className="min-w-0 truncate" title={selected.path}>
                  {selected.old_path ? `${selected.old_path} -> ` : ""}{selected.path}
                </span>
                {selected.old_mode && selected.new_mode && selected.old_mode !== selected.new_mode && (
                  <span className="flex-none text-[10px] text-ink-muted">
                    {selected.old_mode} -&gt; {selected.new_mode}
                  </span>
                )}
              </div>
              <div className="inline-flex flex-none rounded border border-line bg-surface-sunken p-0.5 md:hidden">
                {(["before", "after"] as const).map((side) => (
                  <button key={side} type="button" onClick={() => setMobileSide(side)} className={`rounded px-2 py-1 text-[10px] ${mobileSide === side ? "bg-surface-raised text-ink-strong" : "text-ink-muted"}`}>
                    {side}
                  </button>
                ))}
              </div>
            </div>
            {!selected.inlined ? (
              <div className="m-auto px-6 text-center text-sm text-ink-muted">
                {selected.binary ? "二进制文件不提供文本预览。" : "文件过大，未内联到 diff 产物中。"}
              </div>
            ) : (
              <div className="flex-1 overflow-auto font-mono text-[11px] leading-5">
                <div className="sticky top-0 z-10 grid grid-cols-1 border-b border-line bg-surface-raised text-[10px] font-medium uppercase text-ink-subtle md:grid-cols-2">
                  <div className={`px-3 py-1.5 md:block ${mobileSide === "before" ? "block" : "hidden"}`}>Before</div>
                  <div className={`border-l border-line px-3 py-1.5 md:block ${mobileSide === "after" ? "block" : "hidden"}`}>After</div>
                </div>
                {visibleRows.map((item) => item.hidden ? (
                  <button
                    key={`fold-${item.key}`}
                    type="button"
                    onClick={() => setExpandedRuns((current) => new Set(current).add(item.key - 4))}
                    className="block w-full border-y border-line bg-surface-sunken py-1 text-center text-[10px] text-ink-muted hover:text-ink-strong"
                  >
                    展开 {item.hidden} 行未改动内容
                  </button>
                ) : (
                  <div key={item.key} className="grid min-h-5 grid-cols-1 md:grid-cols-2">
                    <DiffCell side="before" line={item.row?.left} changed={!!item.row?.changed} mobileSide={mobileSide} />
                    <DiffCell side="after" line={item.row?.right} changed={!!item.row?.changed} mobileSide={mobileSide} />
                  </div>
                ))}
              </div>
            )}
          </>
        ) : (
          <div className="m-auto text-sm text-ink-muted">
            {artifact.omitted_files ? "文件清单超过产物大小限制，条目已省略。" : "本次运行没有文件改动。"}
          </div>
        )}
      </section>
    </div>
  );
}

function DiffCell({ side, line, changed, mobileSide }: {
  side: "before" | "after";
  line?: { number?: number; text: string; eofMarker?: boolean };
  changed: boolean;
  mobileSide: "before" | "after";
}) {
  const changedClass = changed
    ? side === "before" ? "bg-state-error-soft" : "bg-state-review-soft"
    : "";
  return (
    <div className={`min-w-0 grid-cols-[48px_minmax(0,1fr)] border-line md:grid ${side === "after" ? "md:border-l" : ""} ${mobileSide === side ? "grid" : "hidden"} ${changedClass}`}>
      <span className="select-none border-r border-line px-2 text-right text-ink-subtle">{line?.number ?? ""}</span>
      <pre className={`min-h-5 overflow-visible whitespace-pre px-2 ${line?.eofMarker ? "italic text-ink-muted" : "text-ink"}`}>{line?.text ?? " "}</pre>
    </div>
  );
}

export function DiffViewerOverlay({ artifact, sessionId, nodeId, name, onClose }: {
  artifact: DiffArtifact;
  sessionId?: string;
  nodeId?: string;
  name: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const listener = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", listener, true);
    return () => window.removeEventListener("keydown", listener, true);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-surface-scrim/60 p-3 backdrop-blur-sm md:p-6" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={name} onClick={(event) => event.stopPropagation()} className="flex h-[88vh] w-[min(1280px,96vw)] flex-col overflow-hidden rounded-lg border border-line bg-surface-raised shadow-modal">
        <DiffHeader artifact={artifact} nodeId={nodeId} name={name} sessionId={sessionId} onClose={onClose} />
        <ConcurrentWarning ids={artifact.concurrent_node_ids} />
        <DiffViewer artifact={artifact} />
      </div>
    </div>
  );
}

export function DiffHeader({ artifact, sessionId, nodeId, name, onClose }: {
  artifact: DiffArtifact;
  sessionId?: string;
  nodeId?: string;
  name: string;
  onClose?: () => void;
}) {
  return (
    <header className="flex min-h-14 items-center justify-between gap-4 border-b border-line bg-surface-raised px-4">
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-ink-strong">{name}</div>
        <div className="font-mono text-[10px] text-ink-subtle">
          {nodeId ? `node ${nodeId.slice(0, 8)} · ` : "working tree · "}{artifact.totals.files} files
        </div>
      </div>
      <div className="flex flex-none items-center gap-1">
        {sessionId && nodeId && (
          <button type="button" title="在新标签页打开" aria-label="在新标签页打开" onClick={() => window.open(markdownRouteUrl({ src: "diff", sessionId, nodeId, name }), "_blank", "noopener")} className="flex h-8 w-8 items-center justify-center rounded border border-line text-ink-muted hover:text-ink-strong">
            <ExternalLink size={15} />
          </button>
        )}
        {onClose && <button type="button" title="关闭" aria-label="关闭" onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded border border-line text-ink-muted hover:text-ink-strong"><X size={16} /></button>}
      </div>
    </header>
  );
}

export function ConcurrentWarning({ ids }: { ids: string[] }) {
  if (!ids.length) return null;
  return (
    <div className="flex items-start gap-2 border-b border-state-waiting/30 bg-state-waiting-soft px-4 py-2 text-[11px] text-state-waiting">
      <AlertTriangle size={14} className="mt-0.5 flex-none" />
      <span>运行期间另有 {ids.length} 个节点在同一工作树执行（{ids.map((id) => id.slice(0, 8)).join(" · ")}），以下改动可能包含它们的产出。</span>
    </div>
  );
}
