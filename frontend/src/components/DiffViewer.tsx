import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowRightLeft,
  ExternalLink,
  FilePenLine,
  FilePlus2,
  FileX2,
  WrapText,
  X,
} from "lucide-react";

import { readDiffWrap, writeDiffWrap, type DiffWrapMode } from "../diffWrap";
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

type DiffLine = { number?: number; text: string; eofMarker?: boolean };

type AlignedLine = {
  left?: DiffLine;
  right?: DiffLine;
  changed: boolean;
};

type DiffSide = "before" | "after";

const DIFF_SIDES = ["before", "after"] as const;

/** A rendered row: an aligned line pair, or a fold standing in for `hidden`
 *  unchanged lines. `key` is unique within one file's visible rows. */
type VisibleRow = { row?: AlignedLine; hidden?: number; key: number };

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
  const [mobileSide, setMobileSide] = useState<DiffSide>("after");
  const [wrap, setWrap] = useState<DiffWrapMode>(readDiffWrap);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const wrapAnchorRef = useRef<RowAnchor | null>(null);
  const selected = artifact.files.find((file) => file.path === selectedPath) ?? artifact.files[0];
  const rows = useMemo(
    () => selected?.inlined
      ? alignLines(selected.before ?? null, selected.after ?? null)
      : [],
    [selected],
  );

  useEffect(() => setExpandedRuns(new Set()), [selected?.path]);

  const visibleRows = useMemo(() => {
    const result: VisibleRow[] = [];
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

  const expandRun = useCallback(
    (foldKey: number) => setExpandedRuns((current) => new Set(current).add(foldKey - 4)),
    [],
  );

  /* Switching modes changes every row's height, so without help the scroll
   * position would land somewhere unrelated. Remember which row sits at the
   * top before the switch and put it back at the same offset afterwards. */
  const toggleWrap = useCallback(() => {
    wrapAnchorRef.current = topVisibleRow(scrollerRef.current);
    setWrap((current) => (current === "wrap" ? "nowrap" : "wrap"));
  }, []);

  useLayoutEffect(() => {
    const anchor = wrapAnchorRef.current;
    wrapAnchorRef.current = null;
    if (anchor && scrollerRef.current) restoreRowAnchor(scrollerRef.current, anchor);
  }, [wrap]);

  useEffect(() => writeDiffWrap(wrap), [wrap]);

  /* Alt+Z, as in VS Code. `code` rather than `key`: on macOS Option+Z types
   * "Ω", so the key value never reads as "z". */
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (event.code !== "KeyZ" || !event.altKey || event.ctrlKey || event.metaKey || event.repeat) return;
      const target = event.target as HTMLElement | null;
      if (target && (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable)) return;
      event.preventDefault();
      toggleWrap();
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [toggleWrap]);

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
              <div className="flex flex-none items-center gap-2">
                {selected.inlined && (
                  <button
                    type="button"
                    aria-pressed={wrap === "wrap"}
                    aria-label="自动换行"
                    title="自动换行（Alt+Z）"
                    onClick={toggleWrap}
                    className={`flex h-7 items-center gap-1.5 rounded border px-2 text-[10px] transition ${wrap === "wrap" ? "border-brand/50 bg-brand-soft text-brand-ink" : "border-line text-ink-muted hover:text-ink-strong"}`}
                  >
                    <WrapText size={13} />
                    <span className="hidden md:inline">自动换行</span>
                  </button>
                )}
                <div className="inline-flex rounded border border-line bg-surface-sunken p-0.5 md:hidden">
                  {DIFF_SIDES.map((side) => (
                    <button key={side} type="button" onClick={() => setMobileSide(side)} className={`rounded px-2 py-1 text-[10px] ${mobileSide === side ? "bg-surface-raised text-ink-strong" : "text-ink-muted"}`}>
                      {side}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            {!selected.inlined ? (
              <div className="m-auto px-6 text-center text-sm text-ink-muted">
                {selected.binary ? "二进制文件不提供文本预览。" : "文件过大，未内联到 diff 产物中。"}
              </div>
            ) : (
              <div ref={scrollerRef} className="flex-1 overflow-y-auto overflow-x-hidden font-mono text-[11px] leading-5">
                <div className="sticky top-0 z-10 grid grid-cols-1 border-b border-line bg-surface-raised text-[10px] font-medium uppercase text-ink-subtle md:grid-cols-2">
                  <div className={`px-3 py-1.5 md:block ${mobileSide === "before" ? "block" : "hidden"}`}>Before</div>
                  <div className={`border-l border-line px-3 py-1.5 md:block ${mobileSide === "after" ? "block" : "hidden"}`}>After</div>
                </div>
                {wrap === "wrap" ? (
                  <WrappedRows items={visibleRows} mobileSide={mobileSide} onExpand={expandRun} />
                ) : (
                  <UnwrappedRows key={selected.path} items={visibleRows} mobileSide={mobileSide} onExpand={expandRun} />
                )}
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

type RowAnchor = { key: string; offset: number };

/** The first row whose bottom edge is below the sticky column header. Rows of
 *  a hidden side (the mobile single-pane view) have no box and are skipped. */
function topVisibleRow(scroller: HTMLElement | null): RowAnchor | null {
  if (!scroller) return null;
  const top = scroller.getBoundingClientRect().top;
  const header = scroller.firstElementChild?.getBoundingClientRect().height ?? 0;
  for (const element of scroller.querySelectorAll<HTMLElement>("[data-row]")) {
    const rect = element.getBoundingClientRect();
    if (rect.height > 0 && rect.bottom > top + header) {
      return { key: element.dataset.row ?? "", offset: rect.top - top };
    }
  }
  return null;
}

function restoreRowAnchor(scroller: HTMLElement, anchor: RowAnchor) {
  const element = Array.from(
    scroller.querySelectorAll<HTMLElement>(`[data-row="${anchor.key}"]`),
  ).find((candidate) => candidate.getBoundingClientRect().height > 0);
  if (!element) return;
  const offset = element.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
  scroller.scrollTop += offset - anchor.offset;
}

function changedBackground(side: DiffSide, changed: boolean): string {
  if (!changed) return "";
  return side === "before" ? "bg-state-error-soft" : "bg-state-review-soft";
}

function mobileVisibility(side: DiffSide, mobileSide: DiffSide): string {
  return `${mobileSide === side ? "block" : "hidden"} md:block`;
}

/* Wrapped mode keeps both sides in one grid row per line pair, so a line that
 * wraps on one side grows the whole row and the next line numbers still meet. */
function WrappedRows({ items, mobileSide, onExpand }: {
  items: VisibleRow[];
  mobileSide: DiffSide;
  onExpand: (foldKey: number) => void;
}) {
  return (
    <>
      {items.map((item) => item.hidden ? (
        <button
          key={`fold-${item.key}`}
          data-row={item.key}
          type="button"
          onClick={() => onExpand(item.key)}
          className="block w-full border-y border-line bg-surface-sunken py-1 text-center text-[10px] text-ink-muted hover:text-ink-strong"
        >
          展开 {item.hidden} 行未改动内容
        </button>
      ) : (
        <div key={item.key} data-row={item.key} className="grid min-h-5 grid-cols-1 md:grid-cols-2">
          <DiffCell side="before" line={item.row?.left} changed={!!item.row?.changed} mobileSide={mobileSide} />
          <DiffCell side="after" line={item.row?.right} changed={!!item.row?.changed} mobileSide={mobileSide} />
        </div>
      ))}
    </>
  );
}

function DiffCell({ side, line, changed, mobileSide }: {
  side: DiffSide;
  line?: DiffLine;
  changed: boolean;
  mobileSide: DiffSide;
}) {
  return (
    <div className={`min-w-0 grid-cols-[48px_minmax(0,1fr)] border-line md:grid ${side === "after" ? "md:border-l" : ""} ${mobileSide === side ? "grid" : "hidden"} ${changedBackground(side, changed)}`}>
      <span className="select-none border-r border-line px-2 text-right text-ink-subtle">{line?.number ?? ""}</span>
      <pre className={`min-h-5 whitespace-pre-wrap px-2 [overflow-wrap:anywhere] ${line?.eofMarker ? "italic text-ink-muted" : "text-ink"}`}>{line?.text ?? " "}</pre>
    </div>
  );
}

/* Unwrapped mode gives each side its own horizontal scroller, so the two sides
 * scroll sideways independently while the outer container scrolls both
 * vertically. Every line is exactly one `h-5` row and both sides render the
 * same row list, which is what keeps them aligned without a shared grid row.
 *
 * A pane is as tall as the whole file, so its native scrollbar would sit below
 * the last line. Panes hide it and a sticky strip at the bottom of the viewport
 * carries a proxy scrollbar per side, kept in step with the pane. */
function UnwrappedRows({ items, mobileSide, onExpand }: {
  items: VisibleRow[];
  mobileSide: DiffSide;
  onExpand: (foldKey: number) => void;
}) {
  const panes = useRef<Record<DiffSide, HTMLDivElement | null>>({ before: null, after: null });
  const proxies = useRef<Record<DiffSide, HTMLDivElement | null>>({ before: null, after: null });
  /** How far each pane can scroll sideways; 0 when its lines fit. */
  const [ranges, setRanges] = useState<Record<DiffSide, number>>({ before: 0, after: 0 });

  useLayoutEffect(() => {
    const measure = () => {
      const next = {
        before: horizontalRange(panes.current.before),
        after: horizontalRange(panes.current.after),
      };
      setRanges((current) =>
        current.before === next.before && current.after === next.after ? current : next,
      );
    };
    measure();
    /* The pane resizes with the viewport (and from nothing when the mobile
     * switch reveals it); its content resizes when folds expand. */
    const observer = new ResizeObserver(measure);
    for (const side of DIFF_SIDES) {
      const pane = panes.current[side];
      if (!pane) continue;
      observer.observe(pane);
      if (pane.firstElementChild) observer.observe(pane.firstElementChild);
    }
    return () => observer.disconnect();
  }, []);

  /* A proxy mounts at scrollLeft 0 when its side starts overflowing. */
  useLayoutEffect(() => {
    for (const side of DIFF_SIDES) follow(panes.current[side], proxies.current[side]);
  }, [ranges]);

  return (
    <>
      <div className="grid grid-cols-1 md:grid-cols-2">
        {DIFF_SIDES.map((side) => (
          <div
            key={side}
            ref={(element) => { panes.current[side] = element; }}
            onScroll={() => follow(panes.current[side], proxies.current[side])}
            className={`diff-pane-scroll overflow-x-auto overflow-y-hidden border-line [container-type:inline-size] ${side === "after" ? "md:border-l" : ""} ${mobileVisibility(side, mobileSide)}`}
          >
            <div className="w-max min-w-full">
              {items.map((item) => item.hidden ? (
                <button
                  key={`fold-${item.key}`}
                  data-row={item.key}
                  type="button"
                  aria-label={`展开 ${item.hidden} 行未改动内容`}
                  onClick={() => onExpand(item.key)}
                  className="block w-full whitespace-nowrap border-y border-line bg-surface-sunken py-1 text-left text-[10px] text-ink-muted hover:text-ink-strong"
                >
                  {/* Pinned to the pane's visible width, so the label stays in
                    * view however far the pane is scrolled. On desktop the
                    * fold reads as one band, so only the left half is labelled. */}
                  <span className={`sticky left-0 inline-block w-[100cqw] text-center ${side === "after" ? "md:invisible" : ""}`}>
                    展开 {item.hidden} 行未改动内容
                  </span>
                </button>
              ) : (
                <UnwrappedLine
                  key={item.key}
                  rowKey={item.key}
                  line={side === "before" ? item.row?.left : item.row?.right}
                  background={changedBackground(side, !!item.row?.changed) || "bg-surface"}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
      {(ranges.before > 0 || ranges.after > 0) && (
        <div className="sticky bottom-0 z-10 grid grid-cols-1 border-t border-line bg-surface-raised md:grid-cols-2">
          {DIFF_SIDES.map((side) => (
            <div key={side} className={mobileVisibility(side, mobileSide)}>
              {ranges[side] > 0 && (
                <div
                  ref={(element) => { proxies.current[side] = element; }}
                  onScroll={() => follow(proxies.current[side], panes.current[side])}
                  aria-hidden
                  className="min-h-[10px] overflow-x-auto overflow-y-hidden"
                >
                  {/* Wider than the proxy by exactly the pane's range, so the
                    * two scroll the same distance and scrollLeft maps 1:1. */}
                  <div className="h-px" style={{ width: `calc(100% + ${ranges[side]}px)` }} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function UnwrappedLine({ rowKey, line, background }: {
  rowKey: number;
  line?: DiffLine;
  background: string;
}) {
  /* The row carries an opaque background so the sticky gutter can inherit it
   * and cover the text scrolling underneath. */
  return (
    <div data-row={rowKey} className={`flex h-5 ${background}`}>
      <span className="sticky left-0 w-[48px] flex-none select-none border-r border-line bg-inherit px-2 text-right text-ink-subtle">
        {line?.number ?? ""}
      </span>
      <pre className={`whitespace-pre px-2 ${line?.eofMarker ? "italic text-ink-muted" : "text-ink"}`}>{line?.text ?? " "}</pre>
    </div>
  );
}

function horizontalRange(pane: HTMLElement | null): number {
  return pane ? Math.max(0, pane.scrollWidth - pane.clientWidth) : 0;
}

/** Copy `from`'s scrollLeft onto `to`. The tolerance stops a pane and its proxy
 *  from echoing sub-pixel values back at each other. */
function follow(from: HTMLElement | null, to: HTMLElement | null) {
  if (from && to && Math.abs(to.scrollLeft - from.scrollLeft) > 0.5) to.scrollLeft = from.scrollLeft;
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
