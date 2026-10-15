/* The standalone code-reading page (`#/code?session=<sid>`).
 *
 * Mounted instead of `App`, like the Markdown page: no workspace socket, no
 * project list, no global shortcuts. Progress comes from polling
 * `/code/index` once a second while a run is in flight.
 *
 * Navigation rides on browser history. Every tab switch or jump pushes a
 * `code-nav` entry; Back/Forward (toolbar, Cmd+[ / ], mouse buttons) replay
 * them through the same `open` as everything else. That is what makes a
 * jump's origin recoverable even though a preview tab may be replaced by
 * its target. Positions are kept in an in-memory table keyed by entry, not
 * written back with `replaceState` on every scroll: Safari rate-limits that,
 * and by the time `popstate` fires the entry being left can no longer be
 * rewritten anyway.
 */

import { ArrowLeft, ArrowRight, CircleAlert, CircleCheck, LoaderCircle, PanelLeft } from "lucide-react";
import { createLowlight, common } from "lowlight";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  ensureCodeIndex,
  getCodeAnalysis,
  getCodeFile,
  getCodeIndex,
  getCodeTree,
  getSession,
} from "../api";
import {
  EMPTY_TABS,
  closeTab,
  isNavEntry,
  openTab,
  pinTab,
  recordPosition,
  retainTabs,
  type NavEntry,
  type TabsState,
} from "../codeTabs";
import {
  FONT_STEPS,
  TREE_WIDTH_DEFAULT,
  clampTreeWidth,
  readCodeFont,
  readStoredReader,
  writeCodeFont,
  writeStoredReader,
} from "../codeReaderStorage";
import type { CodeRoute } from "../codeRoute";
import {
  HIGHLIGHT_MAX_BYTES,
  HIGHLIGHT_MAX_LINES,
  decodeAnalysis,
  hastToLines,
  languageFor,
  type HastRoot,
  type LineSpans,
} from "../codeTokens";
import { CodeView, splitLines, type CodeViewHandle } from "../components/code/CodeView";
import { FileTree, ancestorsOf } from "../components/code/FileTree";
import { TabBar } from "../components/code/TabBar";
import { Stepper } from "../components/Stepper";
import { ThemeToggle } from "../components/ThemeToggle";
import { applyStoredTheme } from "../theme";
import type { CodeAnalysis, CodeFile, CodeIndexStatus, CodeSymbol, CodeTarget, CodeTree } from "../types";

const lowlight = createLowlight(common);

type LoadedFile = {
  file: CodeFile | null;
  error: string | null;
  /** Lexical colour, computed once on load. */
  lexical: LineSpans | null;
  plain: boolean;
  analysis: CodeAnalysis | null;
  semantic: LineSpans | null;
  /** The ETag of `analysis`, to skip a refresh that brought nothing new. */
  etag: string | null;
  /** Bumped per applied analysis, so the view re-renders new spans. */
  revision: number;
  /** waiting: polling for a result; stale: a fresh run still disagreed. */
  analysisState: "none" | "waiting" | "ready" | "stale";
  /** The sha of a result that did not match, to tell "old run" from "edited since". */
  rejected: string | null;
  bannerDismissed: boolean;
  /** The read failed on the network, not on a verdict: offer a retry. */
  retryable: boolean;
};

type PendingReveal = {
  path: string;
  line?: number | null;
  range?: { col: number; endCol: number } | null;
  /** The symbol name expected at `range`, checked before flashing. */
  name?: string;
  scrollTop?: number;
  topLine?: number;
  flash: boolean;
};

function lexicalSpans(file: CodeFile): { spans: LineSpans | null; plain: boolean } {
  const lineCount = splitLines(file.text).length;
  if (file.size > HIGHLIGHT_MAX_BYTES || lineCount > HIGHLIGHT_MAX_LINES) return { spans: null, plain: true };
  const language = languageFor(file.path);
  if (!language || !lowlight.registered(language)) return { spans: null, plain: false };
  try {
    return { spans: hastToLines(lowlight.highlight(language, file.text) as unknown as HastRoot, lineCount), plain: false };
  } catch {
    return { spans: null, plain: false };
  }
}

function fileError(error: unknown): { error: string; retryable: boolean } {
  if (error instanceof ApiError) {
    if (error.status === 403) return { error: "该文件被 .gitignore 忽略或不在项目内", retryable: false };
    if (error.status === 404) return { error: "文件不存在", retryable: false };
    return { error: error.detail ?? error.message, retryable: true };
  }
  return { error: "网络错误，无法读取文件", retryable: true };
}

const EMPTY_FILE: LoadedFile = {
  file: null, error: null, lexical: null, plain: false, analysis: null, semantic: null,
  etag: null, revision: 0, analysisState: "none", rejected: null, bannerDismissed: false, retryable: false,
};

export function CodeReaderPage({ route }: { route: CodeRoute }) {
  const sid = route.sessionId;
  const stored = useMemo(() => readStoredReader(sid), [sid]);
  const [projectName, setProjectName] = useState("");
  const [rootPath, setRootPath] = useState("");
  const [pageError, setPageError] = useState<string | null>(null);
  const [tree, setTree] = useState<CodeTree | null>(null);
  const [treeLoading, setTreeLoading] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(stored?.expanded ?? []));
  const [treeWidth, setTreeWidth] = useState(stored?.treeWidth ?? TREE_WIDTH_DEFAULT);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [narrow, setNarrow] = useState(() => window.innerWidth < 720);
  const [tabs, setTabs] = useState<TabsState>(EMPTY_TABS);
  const [files, setFiles] = useState<Map<string, LoadedFile>>(() => new Map());
  const [status, setStatus] = useState<CodeIndexStatus | null>(null);
  const [font, setFont] = useState(readCodeFont);
  const [toast, setToast] = useState<string | null>(null);
  const [readyFaded, setReadyFaded] = useState(false);
  const [nav, setNav] = useState({ seq: 0, first: 0, max: 0, reloaded: false });

  const view = useRef<CodeViewHandle>(null);
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;
  const filesRef = useRef(files);
  filesRef.current = files;
  const treeRef = useRef(tree);
  treeRef.current = tree;
  /** Position to apply once the active file's view mounts. */
  const pendingReveal = useRef<PendingReveal | null>(null);
  const positions = useRef(new Map<number, { path: string; scrollTop: number; topLine: number }>());
  const navSeq = useRef(0);
  const prioritized = useRef(new Set<string>());

  useEffect(() => applyStoredTheme(), []);
  useEffect(() => writeCodeFont(font), [font]);
  useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth < 720);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 3000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  /* ── project and tree ───────────────────────────────────────────────── */

  useEffect(() => {
    let cancelled = false;
    getSession(sid)
      .then((info) => {
        if (cancelled) return;
        const name = info.name || info.root_path?.split("/").pop() || sid;
        setProjectName(name);
        setRootPath(info.root_path ?? "");
        document.title = `${name} · 代码阅读 · MiniClaw2`;
        if (info.capabilities?.code_reading === false) setPageError("代码阅读仅支持本机绑定的项目");
      })
      .catch(() => !cancelled && setPageError("无法读取项目信息"));
    return () => { cancelled = true; };
  }, [sid]);

  const loadTree = useCallback(async () => {
    setTreeLoading(true);
    try {
      const next = await getCodeTree(sid);
      setTree(next);
      return next;
    } catch (error) {
      setPageError(error instanceof ApiError ? error.detail ?? error.message : "无法读取文件树");
      return null;
    } finally {
      setTreeLoading(false);
    }
  }, [sid]);

  /* ── analysis ───────────────────────────────────────────────────────── */

  const updateFile = useCallback((path: string, update: (file: LoadedFile) => LoadedFile) => {
    const existing = filesRef.current.get(path);
    if (!existing) return;
    const next = new Map(filesRef.current);
    next.set(path, update(existing));
    filesRef.current = next;
    setFiles(next);
  }, []);

  const requestIndex = useCallback(async (paths: string[]) => {
    const fresh = paths.filter((path) => !prioritized.current.has(path));
    fresh.forEach((path) => prioritized.current.add(path));
    try {
      setStatus(await ensureCodeIndex(sid, fresh));
    } catch (error) {
      console.warn("code index request failed", error);
    }
  }, [sid]);

  const fetchAnalysis = useCallback(async (path: string) => {
    const loaded = filesRef.current.get(path);
    const file = loaded?.file;
    if (!file || file.language !== "python" || loaded.plain) return;
    // Re-asked after a run: what is shown already matches this tab's text,
    // so only a different result for that same text replaces it.
    const refresh = loaded.analysisState === "ready";
    try {
      const result = await getCodeAnalysis(sid, path);
      if (refresh) {
        if (result.kind !== "ready" || result.analysis.sha256 !== file.sha256) return;
        if (result.etag !== null && result.etag === filesRef.current.get(path)?.etag) return;
      }
      if (result.kind === "none") {
        updateFile(path, (entry) => ({ ...entry, analysisState: "none" }));
        return;
      }
      if (result.kind === "pending") {
        updateFile(path, (entry) => ({ ...entry, analysisState: "waiting" }));
        void requestIndex([path]);
        return;
      }
      const analysis = result.analysis;
      if (analysis.sha256 !== file.sha256) {
        const current = filesRef.current.get(path);
        if (current?.rejected === analysis.sha256) {
          // A fresh run produced the same mismatch: the file changed after
          // this tab opened. Keep lexical colour and say so.
          updateFile(path, (entry) => ({ ...entry, analysisState: "stale" }));
          return;
        }
        updateFile(path, (entry) => ({ ...entry, analysisState: "waiting", rejected: analysis.sha256 }));
        prioritized.current.delete(path);
        void requestIndex([path]);
        return;
      }
      const semantic = decodeAnalysis(analysis, splitLines(file.text).length);
      const etag = result.etag;
      updateFile(path, (entry) => entry.file !== file ? entry : {
        ...entry, analysis, semantic, etag, revision: entry.revision + 1, analysisState: "ready",
      });
    } catch (error) {
      console.warn("code analysis fetch failed", error);
      if (!refresh) updateFile(path, (entry) => ({ ...entry, analysisState: "none" }));
    }
  }, [requestIndex, sid, updateFile]);

  // Poll while a run is in flight; each tick re-asks for waiting files.
  useEffect(() => {
    if (status?.state !== "running") return;
    const timer = window.setTimeout(async () => {
      try {
        const next = await getCodeIndex(sid);
        setStatus(next);
      } catch {
        /* keep the last status; the next tick retries */
      }
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [sid, status]);

  const progressKey = `${status?.state}:${status?.phase}:${status?.done}:${status?.updated_at}`;
  useEffect(() => {
    if (!status || (status.state !== "running" && status.state !== "ready")) return;
    // A finished run re-resolves a file when a module it imports changed,
    // even though its own text did not: ready tabs re-ask too.
    const finished = status.state === "ready";
    for (const [path, loaded] of filesRef.current) {
      if (loaded.analysisState === "waiting" || (finished && loaded.analysisState === "ready")) void fetchAnalysis(path);
    }
  }, [progressKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (status?.state !== "ready") {
      setReadyFaded(false);
      return;
    }
    const timer = window.setTimeout(() => setReadyFaded(true), 3000);
    return () => window.clearTimeout(timer);
  }, [status?.state]);

  /* ── files ──────────────────────────────────────────────────────────── */

  /* `filesRef` mirrors state for callbacks; writes go through here so the
   * mirror is current before React re-renders (fetchAnalysis reads it). */
  const commitFiles = useCallback((update: (current: Map<string, LoadedFile>) => Map<string, LoadedFile>) => {
    const next = update(filesRef.current);
    if (next === filesRef.current) return;
    filesRef.current = next;
    setFiles(next);
  }, []);

  const loadFile = useCallback(async (path: string) => {
    if (filesRef.current.has(path)) return;
    commitFiles((current) => new Map(current).set(path, EMPTY_FILE));
    let loaded: LoadedFile;
    try {
      const file = await getCodeFile(sid, path);
      let error: string | null = null;
      if (file.binary) error = "二进制文件，无法预览";
      else if (file.too_large) error = "文件超过 2 MiB，无法预览";
      const { spans, plain } = error ? { spans: null, plain: false } : lexicalSpans(file);
      const python = file.language === "python" && !error && !plain;
      loaded = { ...EMPTY_FILE, file, error, lexical: spans, plain, analysisState: python ? "waiting" : "none" };
    } catch (error) {
      loaded = { ...EMPTY_FILE, ...fileError(error) };
    }
    // The tab may have been closed while the read was in flight.
    if (!filesRef.current.has(path)) return;
    commitFiles((current) => new Map(current).set(path, loaded));
    if (loaded.analysisState === "waiting") void fetchAnalysis(path);
  }, [commitFiles, fetchAnalysis, sid]);

  // Only loaded tabs keep data; closed or replaced files are dropped.
  useEffect(() => {
    const open = new Set(tabs.tabs.map((tab) => tab.path));
    commitFiles((current) => {
      const gone = [...current.keys()].filter((path) => !open.has(path));
      if (!gone.length) return current;
      const next = new Map(current);
      gone.forEach((path) => next.delete(path));
      return next;
    });
    if (tabs.active) void loadFile(tabs.active);
  }, [tabs, loadFile, commitFiles]);

  // Reveal the active file's folders in the tree.
  useEffect(() => {
    if (!tabs.active) return;
    const parents = ancestorsOf(tabs.active);
    setExpanded((current) => {
      if (parents.every((parent) => current.has(parent))) return current;
      const next = new Set(current);
      parents.forEach((parent) => next.add(parent));
      return next;
    });
  }, [tabs.active]);

  /* ── navigation ─────────────────────────────────────────────────────── */

  const capture = useCallback(() => {
    const active = tabsRef.current.active;
    const position = view.current?.position();
    if (!active || !position) return null;
    setTabs((state) => recordPosition(state, active, position));
    return { path: active, ...position };
  }, []);

  const pushEntry = useCallback((path: string, scrollTop: number, line: number | null) => {
    const current = capture();
    const currentSeq = navSeq.current;
    if (current) {
      positions.current.set(currentSeq, current);
      const state = window.history.state;
      if (isNavEntry(state)) {
        window.history.replaceState({ ...state, path: current.path, scrollTop: current.scrollTop, line: current.topLine }, "");
      }
    }
    const seq = currentSeq + 1;
    navSeq.current = seq;
    const entry: NavEntry = { kind: "code-nav", seq, path, scrollTop, line };
    window.history.pushState(entry, "");
    setNav((n) => ({ ...n, seq, max: seq }));
  }, [capture]);

  const open = useCallback((
    path: string,
    options: {
      preview: boolean;
      history: boolean;
      line?: number | null;
      range?: { col: number; endCol: number } | null;
      name?: string;
      scrollTop?: number;
      topLine?: number;
      flash?: boolean;
    },
  ) => {
    const sameFile = tabsRef.current.active === path;
    if (options.history) pushEntry(path, options.scrollTop ?? 0, options.line ?? null);
    else if (!sameFile) capture();
    const position = options.line
      ? { topLine: options.line }
      : options.topLine !== undefined || options.scrollTop !== undefined
        ? { scrollTop: options.scrollTop, topLine: options.topLine }
        : undefined;
    setTabs((state) => openTab(state, path, { preview: options.preview, position }));
    if (options.line || options.topLine !== undefined || options.scrollTop !== undefined) {
      pendingReveal.current = {
        path,
        line: options.line,
        range: options.range,
        name: options.name,
        scrollTop: options.scrollTop,
        topLine: options.topLine,
        flash: options.flash ?? Boolean(options.line),
      };
      if (sameFile) window.requestAnimationFrame(() => applyReveal());
    }
  }, [capture, pushEntry]); // eslint-disable-line react-hooks/exhaustive-deps

  const applyReveal = useCallback(() => {
    const pending = pendingReveal.current;
    const active = tabsRef.current.active;
    const loaded = active ? filesRef.current.get(active) : null;
    if (!pending || pending.path !== active || !loaded?.file || !view.current) return;
    pendingReveal.current = null;
    if (pending.line) {
      const lines = splitLines(loaded.file.text);
      const line = Math.min(pending.line, lines.length);
      let range = pending.range ?? null;
      // The target came from the analysed text; if this tab shows older
      // text, only scroll — never flash the wrong word.
      if (range && pending.name !== undefined) {
        const word = (lines[line - 1] ?? "").slice(range.col, range.endCol);
        if (word !== pending.name || line !== pending.line) range = null;
      }
      view.current.reveal(line, range, pending.flash && Boolean(range));
    } else {
      view.current.restore(pending.scrollTop ?? 0, pending.topLine);
    }
  }, []);

  const activeLoaded = tabs.active ? files.get(tabs.active) : undefined;
  useEffect(() => {
    if (activeLoaded?.file) window.requestAnimationFrame(applyReveal);
  }, [activeLoaded?.file, tabs.active, applyReveal]);

  const navigateTo = useCallback((target: CodeTarget, symbol?: CodeSymbol) => {
    const range = target.end_col > target.col ? { col: target.col, endCol: target.end_col } : null;
    open(target.path, {
      preview: true,
      history: true,
      line: target.line,
      range,
      name: symbol?.name,
      flash: true,
    });
  }, [open]);

  useEffect(() => {
    const onPop = (event: PopStateEvent) => {
      const entry = event.state;
      // Re-focusing this tab via the button can fire a null-state popstate.
      if (!isNavEntry(entry)) return;
      const leaving = navSeq.current;
      const current = capture();
      if (current) positions.current.set(leaving, current);
      navSeq.current = entry.seq;
      setNav((n) => ({ ...n, seq: entry.seq, max: Math.max(n.max, entry.seq) }));
      const known = treeRef.current?.files.some((file) => file.path === entry.path) ?? true;
      if (!known) {
        setToast("文件已不在项目中");
        return;
      }
      const saved = positions.current.get(entry.seq);
      open(entry.path, {
        preview: true,
        history: false,
        scrollTop: saved?.scrollTop ?? entry.scrollTop,
        topLine: saved?.topLine ?? (entry.line ?? undefined),
        flash: false,
      });
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [capture, open]);

  /* ── boot: tree, restore, first index ───────────────────────────────── */

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const loaded = await loadTree();
      if (cancelled || !loaded) return;
      const exists = new Set(loaded.files.map((file) => file.path));
      let restored = EMPTY_TABS;
      if (stored) {
        restored = retainTabs(
          { tabs: stored.tabs, active: stored.active, mru: stored.active ? [stored.active] : [] },
          (path) => exists.has(path),
        );
        const dirs = new Set(loaded.files.flatMap((file) => ancestorsOf(file.path)));
        setExpanded(new Set(stored.expanded.filter((path) => dirs.has(path))));
      } else {
        // First visit: only the first level is open.
        setExpanded(new Set());
      }
      setTabs(restored);
      const state = window.history.state;
      if (isNavEntry(state)) {
        navSeq.current = state.seq;
        setNav({ seq: state.seq, first: 0, max: state.seq, reloaded: true });
      } else {
        const active = restored.active;
        const tab = restored.tabs.find((t) => t.path === active);
        window.history.replaceState({
          kind: "code-nav", seq: 0, path: active ?? "", scrollTop: tab?.scrollTop ?? 0, line: tab?.topLine ?? null,
        } satisfies NavEntry, "");
      }
      const activeTab = restored.tabs.find((tab) => tab.path === restored.active);
      if (activeTab) {
        pendingReveal.current = {
          path: activeTab.path, scrollTop: activeTab.scrollTop, topLine: activeTab.topLine, flash: false,
        };
      }
      const python = restored.tabs.map((tab) => tab.path).filter((path) => /\.pyi?$/.test(path));
      python.forEach((path) => prioritized.current.add(path));
      try {
        const next = await ensureCodeIndex(sid, python);
        if (!cancelled) setStatus(next);
      } catch (error) {
        console.warn("code index request failed", error);
      }
    })();
    return () => { cancelled = true; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /* ── persistence ────────────────────────────────────────────────────── */

  const persist = useCallback(() => {
    const position = view.current?.position();
    let state = tabsRef.current;
    if (state.active && position) state = recordPosition(state, state.active, position);
    writeStoredReader(sid, {
      v: 1,
      tabs: state.tabs,
      active: state.active,
      expanded: [...expandedRef.current],
      treeWidth: treeWidthRef.current,
    });
  }, [sid]);
  const expandedRef = useRef(expanded);
  expandedRef.current = expanded;
  const treeWidthRef = useRef(treeWidth);
  treeWidthRef.current = treeWidth;

  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === "hidden") persist();
    };
    const timer = window.setInterval(persist, 2000);
    window.addEventListener("beforeunload", persist);
    document.addEventListener("visibilitychange", onHide);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("beforeunload", persist);
      document.removeEventListener("visibilitychange", onHide);
    };
  }, [persist]);

  /* ── tree width ─────────────────────────────────────────────────────── */

  const startResize = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = treeWidth;
    const onMove = (move: PointerEvent) => setTreeWidth(clampTreeWidth(startWidth + move.clientX - startX));
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  /* ── render ─────────────────────────────────────────────────────────── */

  const fontIndex = FONT_STEPS.indexOf(font as (typeof FONT_STEPS)[number]);
  const stale = useMemo(
    () => new Set([...files].filter(([, loaded]) => loaded.analysisState === "stale").map(([path]) => path)),
    [files],
  );
  const canBack = nav.reloaded || nav.seq > nav.first;
  const canForward = nav.reloaded || nav.seq < nav.max;

  const tree_ = tree && (
    <FileTree
      rootName={tree.root_name}
      entries={tree.files}
      truncated={tree.truncated}
      expanded={expanded}
      activePath={tabs.active}
      onToggle={(path, force) => setExpanded((current) => {
        const next = new Set(current);
        const openNow = force ?? !next.has(path);
        if (openNow) next.add(path);
        else next.delete(path);
        return next;
      })}
      onOpen={(path, pin) => {
        open(path, { preview: !pin, history: true });
        if (pin) setTabs((state) => pinTab(state, path));
        if (narrow) setDrawerOpen(false);
      }}
      onRefresh={() => {
        void loadTree();
        void requestIndex([]);
      }}
      refreshing={treeLoading}
    />
  );

  if (pageError && !tree) {
    return <div className="min-h-screen bg-surface p-6 text-sm text-state-error">{pageError}</div>;
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-surface text-ink">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-surface px-3">
        {narrow && (
          <button
            type="button"
            onClick={() => setDrawerOpen((value) => !value)}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line text-ink-muted hover:bg-surface-raised hover:text-ink"
            title="文件树"
            aria-label="切换文件树"
            aria-expanded={drawerOpen}
          >
            <PanelLeft size={16} strokeWidth={1.7} aria-hidden="true" />
          </button>
        )}
        <div className="flex min-w-0 flex-1 items-baseline gap-2">
          <span className="truncate font-display text-[15px] text-ink-strong">{projectName || "…"}</span>
          <span className="shrink-0 text-[12px] text-ink-muted">· 代码阅读</span>
          {rootPath && !narrow && (
            <span className="truncate font-mono text-[11px] text-ink-subtle" title={rootPath}>{rootPath}</span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div className="inline-flex items-center">
            <button
              type="button"
              onClick={() => window.history.back()}
              disabled={!canBack}
              className="inline-flex h-8 w-8 items-center justify-center rounded-l-md border border-line text-ink-muted hover:bg-surface-raised hover:text-ink disabled:opacity-35"
              title="后退"
              aria-label="后退"
            >
              <ArrowLeft size={15} strokeWidth={1.8} aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => window.history.forward()}
              disabled={!canForward}
              className="-ml-px inline-flex h-8 w-8 items-center justify-center rounded-r-md border border-line text-ink-muted hover:bg-surface-raised hover:text-ink disabled:opacity-35"
              title="前进"
              aria-label="前进"
            >
              <ArrowRight size={15} strokeWidth={1.8} aria-hidden="true" />
            </button>
          </div>
          <StatusPill status={status} faded={readyFaded} onRetry={() => {
            prioritized.current.clear();
            void requestIndex(tabs.tabs.map((tab) => tab.path).filter((path) => /\.pyi?$/.test(path)));
          }} />
          <Stepper
            label={String(font)}
            decGlyph="A−"
            incGlyph="A+"
            onDec={() => setFont(FONT_STEPS[Math.max(0, fontIndex - 1)])}
            onInc={() => setFont(FONT_STEPS[Math.min(FONT_STEPS.length - 1, fontIndex + 1)])}
            onReset={() => setFont(13)}
            atMin={fontIndex <= 0}
            atMax={fontIndex >= FONT_STEPS.length - 1}
            decTitle="缩小字号"
            incTitle="放大字号"
            resetTitle="恢复默认字号"
          />
          <ThemeToggle />
        </div>
      </header>

      <div className="relative flex min-h-0 flex-1">
        {narrow ? (
          drawerOpen && (
            <aside className="absolute inset-y-0 left-0 z-30 w-[min(85vw,320px)] border-r border-line bg-surface-sunken shadow-lg">
              {tree_}
            </aside>
          )
        ) : (
          <>
            <aside style={{ width: treeWidth }} className="shrink-0 bg-surface-sunken">
              {tree_}
            </aside>
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="调整文件树宽度"
              onPointerDown={startResize}
              className="w-1 shrink-0 cursor-col-resize border-l border-line bg-surface-sunken hover:bg-brand/30"
            />
          </>
        )}
        <main className="flex min-w-0 flex-1 flex-col">
          <TabBar
            tabs={tabs.tabs}
            active={tabs.active}
            stale={stale}
            onActivate={(path) => {
              if (path !== tabs.active) open(path, { preview: true, history: true });
            }}
            onPin={(path) => setTabs((state) => pinTab(state, path))}
            onClose={(path) => {
              if (path === tabs.active) capture();
              setTabs((state) => {
                const next = closeTab(state, path);
                const nextTab = next.tabs.find((tab) => tab.path === next.active);
                if (nextTab) {
                  pendingReveal.current = {
                    path: nextTab.path, scrollTop: nextTab.scrollTop, topLine: nextTab.topLine, flash: false,
                  };
                }
                return next;
              });
            }}
          />
          <ActiveFile
            key={tabs.active ?? "none"}
            path={tabs.active}
            loaded={activeLoaded}
            font={font}
            viewRef={view}
            onNavigate={navigateTo}
            onRetry={() => {
              if (!tabs.active) return;
              const path = tabs.active;
              commitFiles((current) => {
                const next = new Map(current);
                next.delete(path);
                return next;
              });
              void loadFile(path);
            }}
            onDismissBanner={() => tabs.active && updateFile(tabs.active, (entry) => ({ ...entry, bannerDismissed: true }))}
          />
        </main>
      </div>
      {toast && (
        <div role="status" className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-md border border-line-strong bg-surface-raised px-3 py-2 text-[12px] text-ink shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}

function StatusPill({ status, faded, onRetry }: { status: CodeIndexStatus | null; faded: boolean; onRetry: () => void }) {
  if (!status || status.total === 0 && status.state !== "running") return null;
  if (status.state === "running") {
    const label = status.phase === "resolve" ? `正在解析引用 ${status.done}/${status.total}` : `正在分析 ${status.done}/${status.total}`;
    return (
      <span
        className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line px-2 text-[11px] text-ink-muted"
        title="首次分析全项目 Python 文件；之后只重算变化的文件"
        role="status"
      >
        <LoaderCircle size={13} strokeWidth={2} className="animate-spin" aria-hidden="true" />
        {label}
      </span>
    );
  }
  if (status.state === "error") {
    return (
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex h-8 items-center gap-1.5 rounded-md border border-state-error/40 bg-state-error-soft px-2 text-[11px] text-state-error"
        title={status.error ?? "分析失败"}
      >
        <CircleAlert size={13} strokeWidth={2} aria-hidden="true" />
        分析失败
      </button>
    );
  }
  if (status.state !== "ready") return null;
  const when = status.updated_at ? new Date(status.updated_at * 1000).toLocaleString() : "";
  const title = `分析完成 · ${when} · ${status.total} 个文件${status.failed > 0 ? ` · ${status.failed} 个文件无法读取` : ""}`;
  return (
    <span
      className={"inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-[11px] text-state-review transition-all duration-500 " + (faded ? "border border-transparent" : "border border-line")}
      title={title}
      role="status"
    >
      <CircleCheck size={13} strokeWidth={2} aria-hidden="true" />
      <span className={faded ? "sr-only" : ""}>分析完成</span>
    </span>
  );
}

type ActiveFileProps = {
  path: string | null;
  loaded: LoadedFile | undefined;
  font: number;
  viewRef: React.RefObject<CodeViewHandle>;
  onNavigate: (target: CodeTarget, symbol?: CodeSymbol) => void;
  onRetry: () => void;
  onDismissBanner: () => void;
};

function ActiveFile({ path, loaded, font, viewRef, onNavigate, onRetry, onDismissBanner }: ActiveFileProps) {
  if (!path) {
    return <div className="flex flex-1 items-center justify-center text-[13px] text-ink-muted">从左侧选择文件开始阅读</div>;
  }
  if (!loaded || (!loaded.file && !loaded.error)) {
    return <div className="flex flex-1 items-center justify-center text-[13px] text-ink-muted">加载中…</div>;
  }
  if (loaded.error || !loaded.file) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 text-[13px] text-ink-muted">
        <span>{loaded.error}</span>
        {loaded.retryable && (
          <button type="button" onClick={onRetry} className="rounded-md border border-line px-3 py-1 text-[12px] text-ink hover:bg-surface-raised">
            重试
          </button>
        )}
      </div>
    );
  }
  const file = loaded.file;
  const semantic = loaded.analysisState === "ready" ? loaded.semantic : null;
  const spans = semantic ?? loaded.lexical;
  const symbols = semantic && loaded.analysis ? loaded.analysis.symbols : [];
  const syntax = semantic ? loaded.analysis?.syntax_error : null;
  return (
    <>
      {loaded.plain && (
        <Banner tone="info">文件较大，已关闭高亮</Banner>
      )}
      {loaded.analysisState === "waiting" && (
        <Banner tone="info">
          <LoaderCircle size={12} strokeWidth={2} className="animate-spin" aria-hidden="true" />
          正在分析此文件…（先显示词法高亮）
        </Banner>
      )}
      {syntax && !loaded.bannerDismissed && (
        <Banner tone="warn" onDismiss={onDismissBanner}>
          第 {syntax.line} 行有语法错误，部分符号可能无法跳转
        </Banner>
      )}
      <CodeView
        ref={viewRef}
        text={file.text}
        spans={spans}
        spansVersion={semantic ? 1 + loaded.revision : spans ? 1 : 0}
        symbols={symbols}
        font={font}
        onNavigate={onNavigate}
      />
    </>
  );
}

function Banner({ tone, children, onDismiss }: { tone: "info" | "warn"; children: React.ReactNode; onDismiss?: () => void }) {
  return (
    <div
      className={
        "flex shrink-0 items-center gap-1.5 border-b px-3 py-1 text-[11.5px] " +
        (tone === "warn"
          ? "border-state-waiting/30 bg-state-waiting-soft text-state-waiting"
          : "border-line bg-surface-sunken text-ink-muted")
      }
      role="status"
    >
      {children}
      {onDismiss && (
        <button type="button" onClick={onDismiss} className="ml-auto text-[11px] hover:underline" aria-label="关闭提示">
          关闭
        </button>
      )}
    </div>
  );
}

export default CodeReaderPage;
