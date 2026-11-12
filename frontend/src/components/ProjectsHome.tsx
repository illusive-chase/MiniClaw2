import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type SetStateAction,
} from "react";
import { Plus, Search } from "lucide-react";

import {
  ArchiveBusyError,
  deleteSession,
  getSelfUpdate,
  listSessions,
  renameSession,
  setSessionArchived,
  updateSessionIcon,
} from "../api";
import { isHumanBlocked, isUnread } from "../activeNodes";
import {
  NO_BADGES,
  commitFolderName,
  dissolve,
  findFolder,
  folderBadges,
  folderOf,
  isFolder,
  moveInFolder,
  moveSlot,
  moveToFolder,
  newFolderId,
  pullOut,
  renameFolder,
  slotKey,
  wrapInNewFolder,
  type SlotBadges,
  type SlotKey,
} from "../homeLayout";
import type {
  ActiveNodeEntry,
  GlobalState,
  HomeFolder,
  HomeItem,
  ModelPreset,
  ProjectIcon,
  SelfUpdateState,
  SessionInfo,
} from "../types";
import { GlobalSettingsModal } from "./GlobalSettingsModal";
import { ThemeToggle } from "./ThemeToggle";
import {
  CornerBadge,
  HomeCell,
  TileFaceView,
  type CellCtx,
  type CornerMark,
  type RingPhase,
  type TileFace,
} from "./home/HomeCell";
import { HomeOverlay } from "./home/HomeOverlay";
import {
  HomeSidebar,
  NEW_FOLDER_OPTION,
  SIDEBAR_WIDTH,
  formatRelative,
  type SidebarSubject,
} from "./home/HomeSidebar";
import { useFlip } from "./home/useFlip";
import { useLatest } from "../motion";
import { FILL_MS, useHomeGestures, type GestureTarget } from "./home/useHomeGestures";
import { useHomeSync } from "./home/useHomeSync";

type Props = {
  onOpen: (session: SessionInfo) => void;
  onCreate: () => void;
  sessions: SessionInfo[] | null;
  setSessions: Dispatch<SetStateAction<SessionInfo[] | null>>;
  modelPresets: ModelPreset[];
  globalState: GlobalState | null;
  onGlobalStateChanged: (state: GlobalState) => void;
  activeEntries: ActiveNodeEntry[];
  readKeys: ReadonlySet<string>;
  /** Header slot: the run-status and notification buttons. */
  headerStatus?: ReactNode;
  /** Overlays the grid, anchored below the header rather than scrolling with it. */
  noticeRail?: ReactNode;
};

type Selection = { kind: "project"; id: string } | { kind: "folder"; id: string };

const ARCHIVE_SLOT = "sys:archive";
const WIDE_QUERY = "(min-width: 960px)";
const TOAST_MS = 2400;

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [query]);
  return matches;
}

/* Spread jiggle phases so neighbours never swing in step. */
function jiggleDelay(slot: string): number {
  let hash = 0x811c9dc5;
  for (let index = 0; index < slot.length; index += 1) {
    hash ^= slot.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return -((hash % 7) * 30);
}

function statusParts(badges: SlotBadges, extra: { unbound?: boolean } = {}): string[] {
  const parts: string[] = [];
  if (badges.waiting) parts.push(`${badges.waiting} 个节点等你处理`);
  if (badges.errors) parts.push(`${badges.errors} 个出错`);
  if (badges.running) parts.push(`${badges.running} 个在跑`);
  if (extra.unbound) parts.push("此设备未绑定");
  return parts;
}

/* Unbound wins the corner: with no local path, nothing the badges point at
 * can be acted on from this device. */
function cornerMark(badges: SlotBadges, unbound: boolean): CornerMark {
  if (unbound) return { kind: "unbound" };
  if (badges.waiting > 0) return { kind: "waiting", count: badges.waiting };
  if (badges.errors > 0) return { kind: "error" };
  return null;
}

function displayName(session: SessionInfo): string {
  return session.name?.trim() || "未命名";
}

export function ProjectsHome({
  onOpen,
  onCreate,
  sessions,
  setSessions,
  modelPresets,
  globalState,
  onGlobalStateChanged,
  activeEntries,
  readKeys,
  headerStatus,
  noticeRail,
}: Props) {
  const [error, setError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [selfUpdate, setSelfUpdate] = useState<SelfUpdateState | null>(null);
  const [editing, setEditing] = useState(false);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [openFolderId, setOpenFolderId] = useState<string | null>(null);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [toast, setToast] = useState<{ id: number; text: string } | null>(null);
  const [focusFolderName, setFocusFolderName] = useState(false);
  const wide = useMediaQuery(WIDE_QUERY);

  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const gridRef = useRef<HTMLDivElement | null>(null);
  const folderGridRef = useRef<HTMLDivElement | null>(null);
  const folderPanelRef = useRef<HTMLDivElement | null>(null);
  const ghostRef = useRef<HTMLDivElement | null>(null);
  const archivePanelRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  const all = sessions ?? [];
  const byId = useMemo(() => new Map(all.map((session) => [session.id, session])), [all]);
  const archived = useMemo(
    () => all.filter((session) => session.archived_at).sort((a, b) => b.archived_at! - a.archived_at!),
    [all],
  );
  /* Oldest first: the order a project the stored layout does not list yet
   * joins at the end — the same rule the server applies on read. */
  const activeIds = useMemo(
    () => all
      .filter((session) => !session.archived_at)
      .sort((a, b) => a.created_at - b.created_at || a.id.localeCompare(b.id))
      .map((session) => session.id),
    [all],
  );
  const home = useHomeSync(activeIds);
  const items = home.items;

  const refresh = useCallback(async () => {
    try {
      setSessions(await listSessions());
    } catch (err) {
      setError(String(err));
    }
  }, [setSessions]);

  useEffect(() => {
    void refresh();
    const onFocus = () => {
      void refresh();
      void home.refresh();
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refresh, home.refresh]);

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

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), TOAST_MS);
    return () => window.clearTimeout(timer);
  }, [toast]);
  const say = useCallback((text: string) => setToast({ id: Date.now(), text }), []);

  const badges = useMemo(() => {
    const map = new Map<string, SlotBadges>();
    for (const entry of activeEntries) {
      const current = map.get(entry.project_id) ?? { ...NO_BADGES };
      if (isHumanBlocked(entry)) current.waiting += 1;
      else if (entry.state === "error" && isUnread(entry, readKeys)) current.errors += 1;
      else if (entry.state === "running") current.running += 1;
      map.set(entry.project_id, current);
    }
    return map;
  }, [activeEntries, readKeys]);

  const folders = useMemo(
    () => (items ?? []).filter(isFolder).map((item) => item.folder),
    [items],
  );

  /* ---- selection and modes ---- */

  /* Edit mode and the sidebar are one state: the sidebar is open exactly
   * while editing. Selecting only changes what it shows. */
  const enterEdit = useCallback((next: Selection | null) => {
    setEditing(true);
    if (next) setSelection(next);
  }, []);

  const exitEdit = useCallback(() => {
    setEditing(false);
    setSelection(null);
    setFocusFolderName(false);
  }, []);

  const selectFromSlot = useCallback((slot: string): Selection | null => {
    if (slot.startsWith("p:")) return { kind: "project", id: slot.slice(2) };
    if (slot.startsWith("f:")) return { kind: "folder", id: slot.slice(2) };
    return null;
  }, []);

  /* ---- layout edits ---- */

  const sync = home.apply;

  const place = useCallback((projectId: string, target: string) => {
    if (!items) return;
    const current = folderOf(items, projectId);
    if ((current?.id ?? "") === target) return;
    if (target === NEW_FOLDER_OPTION) {
      const folderId = newFolderId(items);
      sync((next) => wrapInNewFolder(next, projectId, folderId));
      setOpenFolderId(null);
      setSelection({ kind: "folder", id: folderId });
      setFocusFolderName(true);
      say("已新建组，给它起个名字");
      return;
    }
    if (target) {
      sync((next) => moveToFolder(next, projectId, target));
      say(`已移入「${findFolder(items, target)?.name ?? ""}」`);
    } else {
      sync((next) => pullOut(next, projectId));
      say(`已移出「${current?.name ?? ""}」，放在组的后面`);
    }
  }, [items, say, sync]);

  /* Folder names type straight into local state; only the committed (trimmed,
   * non-empty) name is queued for the server. */
  const renameFolderLive = useCallback((folderId: string, name: string) => {
    if (!items) return;
    home.preview(renameFolder(items, folderId, name));
  }, [home, items]);

  const commitFolderRename = useCallback((folderId: string) => {
    if (!items) return;
    const folder = findFolder(items, folderId);
    if (!folder) return;
    const name = commitFolderName(folder.name);
    sync((next) => renameFolder(next, folderId, name));
    setFocusFolderName(false);
  }, [items, sync]);

  const dissolveFolder = useCallback((folderId: string) => {
    const folder = items ? findFolder(items, folderId) : null;
    sync((next) => dissolve(next, folderId));
    setOpenFolderId(null);
    setSelection(null);
    if (folder) say(`已解散「${folder.name}」`);
  }, [items, say, sync]);

  /* ---- project record edits ---- */

  const patchSession = useCallback((updated: SessionInfo) => {
    setSessions((prev) => (prev ? prev.map((session) => (session.id === updated.id ? updated : session)) : prev));
  }, [setSessions]);

  const onRename = useCallback(async (id: string, name: string) => {
    try {
      const updated = await renameSession(id, name);
      setSessions((prev) => prev ? prev.map((session) => (session.id === id ? { ...session, name: updated.name } : session)) : prev);
    } catch (err) {
      setError(String(err));
    }
  }, [setSessions]);

  const onIcon = useCallback(async (id: string, icon: ProjectIcon) => {
    const before = byId.get(id)?.icon;
    setSessions((prev) => prev ? prev.map((session) => (session.id === id ? { ...session, icon } : session)) : prev);
    try {
      patchSession(await updateSessionIcon(id, icon));
    } catch (err) {
      setError(String(err));
      if (before) {
        setSessions((prev) => prev ? prev.map((session) => (session.id === id ? { ...session, icon: before } : session)) : prev);
      }
    }
  }, [byId, patchSession, setSessions]);

  const onArchive = useCallback(async (id: string, archive: boolean) => {
    try {
      patchSession(await setSessionArchived(id, archive));
      setSelection(null);
      if (archive) {
        setOpenFolderId(null);
        say(`已归档「${displayName(byId.get(id)!)}」，可在主屏末尾的「已归档」里找回`);
      } else {
        say("已取消归档，放回主屏末尾");
      }
      void home.refresh();
    } catch (err) {
      setError(
        err instanceof ArchiveBusyError
          ? `还有 ${err.busy.length} 个节点在运行或等待，结束后才能归档`
          : String(err),
      );
    }
  }, [byId, home, patchSession, say]);

  const onDelete = useCallback(async (id: string) => {
    setSelection(null);
    setSessions((prev) => (prev ? prev.filter((session) => session.id !== id) : prev));
    try {
      await deleteSession(id);
      void home.refresh();
    } catch (err) {
      setError(String(err));
      void refresh();
    }
  }, [home, refresh, setSessions]);

  const openProject = useCallback((session: SessionInfo) => {
    exitEdit();
    onOpen(session);
  }, [exitEdit, onOpen]);

  /* ---- gestures ---- */

  const rootFlip = useFlip(gridRef);
  const folderFlip = useFlip(folderGridRef);

  const activate = useCallback((target: GestureTarget) => {
    const { slot, ctx } = target;
    if (slot === ARCHIVE_SLOT) {
      setArchiveOpen(true);
      setOpenFolderId(null);
      return;
    }
    if (slot.startsWith("f:")) {
      const folderId = slot.slice(2);
      setOpenFolderId(folderId);
      setArchiveOpen(false);
      if (editing) setSelection({ kind: "folder", id: folderId });
      return;
    }
    const session = byId.get(slot.slice(2));
    if (!session) return;
    if (editing || session.archived_at || ctx === "archive") {
      enterEdit({ kind: "project", id: session.id });
      return;
    }
    openProject(session);
  }, [byId, editing, enterEdit, openProject]);

  const gestures = useHomeGestures({
    editing,
    items,
    openFolderId,
    rootGrid: gridRef,
    rootFlip,
    rootScroller: scrollerRef,
    folderGrid: folderGridRef,
    folderFlip,
    folderPanel: folderPanelRef,
    ghost: ghostRef,
    preview: home.preview,
    hold: home.hold,
    commit: (edit) => sync(edit, { applied: true }),
    onActivate: activate,
    onArmedRelease: (target) => {
      if (target.slot === ARCHIVE_SLOT) {
        setArchiveOpen(true);
        return;
      }
      enterEdit(selectFromSlot(target.slot));
    },
    onDragStart: (target) => {
      const next = selectFromSlot(target.slot);
      if (next) setSelection(next);
      setFocusFolderName(false);
    },
    onLeftFolder: () => setOpenFolderId(null),
    onMerged: ({ folderId, created }) => {
      if (created) {
        setOpenFolderId(folderId);
        setSelection({ kind: "folder", id: folderId });
        setFocusFolderName(true);
        say("已建组，给它起个名字");
      } else {
        setSelection({ kind: "folder", id: folderId });
        say(`已移入「${findFolder(items ?? [], folderId)?.name ?? ""}」`);
      }
    },
  });

  const onContextMenu = useCallback((target: GestureTarget, event: React.MouseEvent) => {
    event.preventDefault();
    if (gestures.drag) return;
    if (target.slot === ARCHIVE_SLOT) {
      setArchiveOpen(true);
      return;
    }
    enterEdit(selectFromSlot(target.slot));
  }, [enterEdit, gestures.drag, selectFromSlot]);

  /* ---- keyboard ---- */

  const keyboardMove = useCallback((slot: string, ctx: CellCtx, step: number) => {
    if (!items) return;
    if (ctx === "folder" && openFolderId) {
      const folder = findFolder(items, openFolderId);
      const projectId = slot.slice(2);
      if (!folder) return;
      const to = folder.projects.indexOf(projectId) + step;
      const folderId = openFolderId;
      sync((next) => moveInFolder(next, folderId, projectId, to));
      return;
    }
    const key = slot as SlotKey;
    const to = items.findIndex((item) => slotKey(item) === key) + step;
    sync((next) => moveSlot(next, key, to));
  }, [items, openFolderId, sync]);

  const onCellKeyDown = useCallback((target: GestureTarget, draggable: boolean, event: ReactKeyboardEvent<HTMLDivElement>) => {
    const { slot, ctx } = target;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activate(target);
      return;
    }
    if (event.key === "F2") {
      event.preventDefault();
      if (slot === ARCHIVE_SLOT) setArchiveOpen(true);
      else enterEdit(selectFromSlot(slot));
      return;
    }
    const directions: Record<string, "l" | "r" | "u" | "d"> = {
      ArrowLeft: "l", ArrowRight: "r", ArrowUp: "u", ArrowDown: "d",
    };
    const direction = directions[event.key];
    if (!direction) return;
    event.preventDefault();
    const container = event.currentTarget.parentElement;
    if (!container) return;
    const cells = [...container.querySelectorAll<HTMLElement>(":scope > [data-slot]")];
    const top = cells[0]?.offsetTop ?? 0;
    const columns = Math.max(1, cells.findIndex((cell) => cell.offsetTop !== top) === -1
      ? cells.length
      : cells.findIndex((cell) => cell.offsetTop !== top));
    const step = { l: -1, r: 1, u: -columns, d: columns }[direction];
    if (event.altKey && editing && draggable) {
      keyboardMove(slot, ctx, step);
      return;
    }
    cells[cells.indexOf(event.currentTarget) + step]?.focus();
  }, [activate, editing, enterEdit, keyboardMove, selectFromSlot]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing = event.target instanceof HTMLElement && event.target.matches("input, textarea, select");
      /* A modal above the home screen (settings, new project) owns the keys. */
      if (settingsOpen || document.querySelector(".mc-modal[data-phase='enter']")) return;
      if (event.key === "Escape") {
        if (gestures.cancelDrag()) {
          say("已取消拖动");
          return;
        }
        if (event.target === searchRef.current && query) {
          setQuery("");
          return;
        }
        if (typing) {
          (event.target as HTMLElement).blur();
          return;
        }
        if (openFolderId || archiveOpen) {
          setOpenFolderId(null);
          setArchiveOpen(false);
          return;
        }
        if (editing) exitEdit();
        return;
      }
      if (!typing && (event.key === "/" || ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k"))) {
        event.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [archiveOpen, editing, exitEdit, gestures, openFolderId, query, say, settingsOpen]);

  /* A folder that vanished (dissolved, last member pulled out, synced away)
   * closes its overlay and drops out of the sidebar. */
  useEffect(() => {
    if (!items) return;
    if (openFolderId && !findFolder(items, openFolderId)) setOpenFolderId(null);
    if (selection?.kind === "folder" && !findFolder(items, selection.id)) setSelection(null);
  }, [items, openFolderId, selection]);
  useEffect(() => {
    if (selection?.kind === "project" && sessions && !byId.has(selection.id)) setSelection(null);
  }, [byId, selection, sessions]);
  useEffect(() => {
    if (archiveOpen && sessions && archived.length === 0) setArchiveOpen(false);
  }, [archiveOpen, archived.length, sessions]);

  /* ---- cells ---- */

  const ringOf = (slot: string, ctx: CellCtx): RingPhase => {
    const live = gestures.ringFor(slot, ctx);
    if (live) return live;
    if (!editing || !selection || gestures.drag) return "idle";
    const selected = selection.kind === "project" ? `p:${selection.id}` : `f:${selection.id}`;
    return selected === slot && (ctx === "root" || ctx === "folder") ? "selected" : "idle";
  };

  const projectCell = (session: SessionInfo, ctx: CellCtx) => {
    const slot = `p:${session.id}`;
    const projectBadges = session.archived_at ? NO_BADGES : badges.get(session.id) ?? NO_BADGES;
    const unbound = !session.bound_here;
    const parts = statusParts(projectBadges, { unbound });
    parts.push(
      session.archived_at
        ? `归档于 ${formatRelative(session.archived_at)}`
        : `最近活动 ${formatRelative(session.last_activity_at ?? session.created_at)}`,
    );
    const draggable = editing && (ctx === "root" || ctx === "folder");
    const target = { slot, ctx };
    return (
      <HomeCell
        key={slot}
        slot={slot}
        ctx={ctx}
        face={{ kind: "project", session }}
        label={displayName(session)}
        title={`${displayName(session)}\n${parts.join(" · ")}${draggable ? "\n拖动排列；拖到另一个项目上建组" : ""}`}
        ariaLabel={`${displayName(session)}，${parts.join(" · ")}`}
        mark={cornerMark(projectBadges, unbound)}
        running={projectBadges.running > 0}
        dimmed={unbound || !!session.archived_at}
        draggable={draggable}
        ring={ringOf(slot, ctx)}
        holdMs={FILL_MS}
        placeholder={gestures.drag?.slot === slot && gestures.drag.ctx === ctx}
        mergeTarget={gestures.mergeSlot === slot}
        jiggleDelayMs={jiggleDelay(slot)}
        onPointerDown={(event) => gestures.onPointerDown(target, draggable, event)}
        onContextMenu={(event) => onContextMenu(target, event)}
        onKeyDown={(event) => onCellKeyDown(target, draggable, event)}
      />
    );
  };

  const folderCell = (folder: HomeFolder) => {
    const slot = `f:${folder.id}`;
    const members = folder.projects.map((id) => byId.get(id)).filter((s): s is SessionInfo => !!s);
    const total = folderBadges(folder, badges);
    const parts = [`${folder.projects.length} 个项目`, ...statusParts(total)];
    const name = folder.name.trim() || "未命名组";
    const target = { slot, ctx: "root" as const };
    return (
      <HomeCell
        key={slot}
        slot={slot}
        ctx="root"
        face={{ kind: "folder", folder, members }}
        label={name}
        title={`${name}\n${parts.join(" · ")}`}
        ariaLabel={`组 ${name}，${parts.join("，")}`}
        mark={cornerMark(total, false)}
        running={total.running > 0}
        dimmed={false}
        draggable={editing}
        ring={ringOf(slot, "root")}
        holdMs={FILL_MS}
        placeholder={gestures.drag?.slot === slot}
        mergeTarget={gestures.mergeSlot === slot}
        jiggleDelayMs={jiggleDelay(slot)}
        onPointerDown={(event) => gestures.onPointerDown(target, editing, event)}
        onContextMenu={(event) => onContextMenu(target, event)}
        onKeyDown={(event) => onCellKeyDown(target, editing, event)}
      />
    );
  };

  const archiveCell = () => {
    const target = { slot: ARCHIVE_SLOT, ctx: "root" as const };
    return (
      <HomeCell
        key={ARCHIVE_SLOT}
        slot={ARCHIVE_SLOT}
        ctx="root"
        face={{ kind: "archive", count: archived.length }}
        label={`已归档 · ${archived.length}`}
        title={`已归档的项目（${archived.length}）`}
        ariaLabel={`已归档的 ${archived.length} 个项目`}
        mark={null}
        running={false}
        dimmed={false}
        draggable={false}
        ring={ringOf(ARCHIVE_SLOT, "root")}
        holdMs={FILL_MS}
        placeholder={false}
        mergeTarget={false}
        jiggleDelayMs={0}
        onPointerDown={(event) => gestures.onPointerDown(target, false, event, false)}
        onContextMenu={(event) => onContextMenu(target, event)}
        onKeyDown={(event) => onCellKeyDown(target, false, event)}
      />
    );
  };

  const renderItem = (item: HomeItem) => {
    if (isFolder(item)) return folderCell(item.folder);
    const session = byId.get(item.project);
    return session ? projectCell(session, "root") : null;
  };

  /* ---- search ---- */

  const needle = query.trim().toLowerCase();
  const searchHits = useMemo(() => {
    if (!needle || !items) return [];
    const order = [...items.flatMap((item) => (isFolder(item) ? item.folder.projects : [item.project])), ...archived.map((s) => s.id)];
    return order
      .map((id) => byId.get(id))
      .filter((session): session is SessionInfo => !!session && (session.name ?? "").toLowerCase().includes(needle));
  }, [archived, byId, items, needle]);
  const searchMeta = (() => {
    if (!needle) return "";
    if (!searchHits.length) return `没有名称包含「${query.trim()}」的项目`;
    const inFolder = searchHits.filter((session) => items && folderOf(items, session.id)).length;
    const inArchive = searchHits.filter((session) => session.archived_at).length;
    const extra = [inFolder ? `${inFolder} 个在组内` : "", inArchive ? `${inArchive} 个已归档` : ""].filter(Boolean).join("，");
    return `${searchHits.length} 个项目匹配「${query.trim()}」${extra ? `（${extra}）` : ""} · Enter 打开第一个 · Esc 清除`;
  })();

  /* ---- sidebar subject ---- */

  const subject: SidebarSubject | null = (() => {
    if (!selection || !items) return null;
    if (selection.kind === "project") {
      const session = byId.get(selection.id);
      if (!session) return null;
      const projectBadges = badges.get(session.id) ?? NO_BADGES;
      return {
        kind: "project",
        session,
        folders,
        currentFolderId: folderOf(items, session.id)?.id ?? null,
        modelPresets,
        statusLine: statusParts(projectBadges).join(" · "),
        onRename: (name) => void onRename(session.id, name),
        onIcon: (icon) => void onIcon(session.id, icon),
        onPlace: (target) => place(session.id, target),
        onOpen: () => openProject(session),
        onArchive: (archive) => void onArchive(session.id, archive),
        onDelete: () => void onDelete(session.id),
      };
    }
    const folder = findFolder(items, selection.id);
    if (!folder) return null;
    return {
      kind: "folder",
      folder,
      members: folder.projects.map((id) => byId.get(id)).filter((s): s is SessionInfo => !!s),
      autoFocusName: focusFolderName,
      onRename: (name) => renameFolderLive(folder.id, name),
      onCommitName: () => commitFolderRename(folder.id),
      onSelectMember: (projectId) => {
        setOpenFolderId(folder.id);
        setSelection({ kind: "project", id: projectId });
      },
      onDissolve: () => dissolveFolder(folder.id),
    };
  })();
  const subjectKey = selection ? `${selection.kind}:${selection.id}` : null;

  /* ---- header bits ---- */

  const remoteBehind = globalState?.sync.remote?.behind ?? 0;
  const sourceBehind = selfUpdate?.behind ?? 0;
  const describeRef = (at: number | null | undefined) => (at ? new Date(at * 1000).toLocaleString() : "未知时间");
  const remoteStatusTitle = [
    remoteBehind > 0 ? `元数据远端引用落后 ${remoteBehind} 个提交；引用更新于 ${describeRef(globalState?.sync.remote?.ref_at)}` : null,
    sourceBehind > 0 ? `源码远端引用落后 ${sourceBehind} 个提交；引用更新于 ${describeRef(selfUpdate?.ref_at)}` : null,
  ].filter(Boolean).join("\n") || undefined;

  const sidebarInset = editing && wide ? SIDEBAR_WIDTH : 0;
  const openFolder = openFolderId && items ? findFolder(items, openFolderId) : null;
  /* While the overlay fades out its folder may already be gone (dissolved,
   * emptied, or the dragged item just pulled out): keep drawing the last one,
   * minus whatever is now being dragged on the root grid. */
  const shownFolder = useLatest(openFolder);
  const shownMembers = (shownFolder?.projects ?? []).filter(
    (id) => openFolder || gestures.drag?.slot !== `p:${id}`,
  );
  const ghostFace: TileFace | null = (() => {
    const drag = gestures.drag;
    if (!drag) return null;
    if (drag.slot.startsWith("p:")) {
      const session = byId.get(drag.slot.slice(2));
      return session ? { kind: "project", session } : null;
    }
    const folder = items ? findFolder(items, drag.slot.slice(2)) : null;
    return folder
      ? { kind: "folder", folder, members: folder.projects.map((id) => byId.get(id)).filter((s): s is SessionInfo => !!s) }
      : null;
  })();
  const dragGhostMark: CornerMark = (() => {
    const drag = gestures.drag;
    if (!drag || !drag.slot.startsWith("p:")) return null;
    const session = byId.get(drag.slot.slice(2));
    return session ? cornerMark(badges.get(session.id) ?? NO_BADGES, !session.bound_here) : null;
  })();
  const loaded = sessions !== null && items !== null;
  const shownError = error ?? home.error;

  return (
    <div className={"flex h-full flex-col bg-surface text-ink " + (editing ? "home-editing" : "")}>
      <header className="relative z-30 flex h-[60px] flex-none items-center gap-3 border-b border-line bg-surface-raised pl-6 pr-5">
        <div className="flex items-center gap-[9px] whitespace-nowrap font-display text-[19px] font-semibold tracking-tight text-ink-strong">
          <span className="inline-block h-2 w-2 rounded-full bg-brand" />
          Projects
        </div>
        <label
          className="ml-3 flex h-[34px] min-w-0 flex-[0_1_340px] items-center gap-2 rounded-[10px] border border-line bg-surface-sunken px-2.5 text-ink-muted transition-colors duration-[140ms] focus-within:border-brand"
          title="搜索项目（/ 或 ⌘K）"
        >
          <Search size={15} className="flex-none" />
          <input
            ref={searchRef}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              const first = searchHits[0];
              if (event.key === "Enter" && first) {
                event.preventDefault();
                activate({ slot: `p:${first.id}`, ctx: "search" });
              } else if (event.key === "ArrowDown" && first) {
                event.preventDefault();
                document.querySelector<HTMLElement>('[data-ctx="search"]')?.focus();
              }
            }}
            placeholder="搜索项目"
            aria-label="搜索项目"
            autoComplete="off"
            spellCheck={false}
            className="min-w-0 flex-1 border-0 bg-transparent text-[13px] text-ink-strong outline-none placeholder:text-ink-subtle"
          />
          <span className="hidden rounded border border-line-strong px-[5px] font-mono text-[11px] leading-4 text-ink-subtle sm:inline">/</span>
        </label>
        <div className="flex-1" />
        {editing && (
          <span className="home-sidebar-hint hidden whitespace-nowrap text-[12px] text-ink-muted lg:inline">
            拖动排列 · 拖到另一个项目上建组 · Esc 完成
          </span>
        )}
        <div className="flex items-center gap-2">
          {headerStatus}
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            className="relative inline-flex h-8 items-center rounded-md border border-line bg-surface-raised px-3 text-[12.5px] font-medium text-ink-muted transition hover:border-line-strong hover:text-ink"
            title={remoteStatusTitle}
          >
            Global settings
            {remoteStatusTitle && (
              <span
                className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full border-2 border-surface-raised bg-state-waiting"
                aria-label={remoteStatusTitle}
              />
            )}
          </button>
          <ThemeToggle />
          {editing ? (
            <button
              type="button"
              onClick={exitEdit}
              className="inline-flex h-[34px] items-center gap-1.5 rounded-[9px] bg-brand px-3.5 text-[13px] font-medium text-white transition-[filter,transform] duration-[90ms] hover:brightness-95 active:scale-[.97]"
            >
              完成
            </button>
          ) : (
            <button
              type="button"
              onClick={onCreate}
              className="inline-flex h-[34px] items-center gap-1.5 rounded-[9px] bg-brand px-3.5 text-[13px] font-medium text-white transition-[filter,transform] duration-[90ms] hover:brightness-95 active:scale-[.97]"
            >
              <Plus size={15} />
              新建项目
            </button>
          )}
        </div>
      </header>

      <div className="relative flex min-h-0 flex-1 overflow-hidden">
        {noticeRail}
        <div
          ref={scrollerRef}
          className="home-grid-shell flex-1 overflow-y-auto overflow-x-hidden"
          style={{ paddingRight: sidebarInset }}
          onPointerDown={(event) => {
            /* A tap on empty grid space ends editing, as on a phone. */
            if (
              editing
              && !gestures.drag
              && !(event.target as HTMLElement).closest("[data-slot], button, input, a")
            ) {
              const start = { x: event.clientX, y: event.clientY };
              const up = (release: PointerEvent) => {
                window.removeEventListener("pointerup", up);
                if (Math.hypot(release.clientX - start.x, release.clientY - start.y) < 6) exitEdit();
              };
              window.addEventListener("pointerup", up);
            }
          }}
        >
          {shownError && (
            <div className="mx-auto mt-4 max-w-[1160px] px-8">
              <div className="flex items-start justify-between gap-3 rounded-md border border-state-error/30 bg-state-error-soft px-3 py-2 text-xs text-state-error">
                <span>{shownError}</span>
                <button
                  type="button"
                  onClick={() => {
                    setError(null);
                    home.setError(null);
                  }}
                  className="flex-none font-medium"
                >
                  关闭
                </button>
              </div>
            </div>
          )}
          {needle && (
            <div className="mx-auto max-w-[min(1160px,calc(100vw-360px))] px-8 pt-[22px] text-[12px] text-ink-muted">
              {searchMeta}
            </div>
          )}
          {needle ? (
            <div
              className="mx-auto grid max-w-[1160px] grid-cols-[repeat(auto-fill,96px)] content-start justify-center gap-x-2.5 gap-y-[18px] px-8 pb-[140px] pt-[18px]"
              aria-label="搜索结果"
            >
              {searchHits.map((session) => projectCell(session, "search"))}
            </div>
          ) : (
            <div
              ref={gridRef}
              className="relative mx-auto grid min-h-full grid-cols-[repeat(auto-fill,96px)] content-start justify-center gap-x-2.5 gap-y-[18px] px-4 pb-[140px] pt-10 sm:px-8"
              style={{ maxWidth: wide ? `min(1160px, calc(100vw - ${SIDEBAR_WIDTH}px))` : 1160 }}
              aria-label="项目主屏"
            >
              {loaded && items!.map(renderItem)}
              {loaded && archived.length > 0 && archiveCell()}
              {loaded && items!.length === 0 && archived.length === 0 && (
                <div className="col-span-full py-[60px] text-center text-[13px] text-ink-muted">
                  还没有项目。点右上角「新建项目」开始。
                </div>
              )}
              {!loaded && !shownError && (
                <div className="col-span-full py-[60px] text-center text-[12px] text-ink-muted">Loading…</div>
              )}
            </div>
          )}
          {!editing && !needle && loaded && (items!.length > 0 || archived.length > 0) && (
            <div className="pointer-events-none absolute inset-x-0 bottom-[22px] text-center text-[11.5px] text-ink-subtle" style={{ right: sidebarInset }}>
              单击打开 · 长按（或右键）进入编辑，可拖动排列与分组
            </div>
          )}
        </div>

        <HomeOverlay
          open={!!openFolder}
          title={shownFolder?.name.trim() || "未命名组"}
          subtitle={`${shownFolder?.projects.length ?? 0} 个项目`}
          footer={editing ? "在组内拖动可调整顺序 · 拖出面板并停留片刻可移出组" : "点空白处关闭 · 长按项目进入编辑"}
          panelRef={folderPanelRef}
          leaving={gestures.leavingFolder}
          rightInset={sidebarInset}
          onBackdrop={() => setOpenFolderId(null)}
        >
          <div
            ref={folderGridRef}
            className="grid grid-cols-[repeat(auto-fill,96px)] content-start justify-center gap-x-2.5 gap-y-[18px] p-1"
          >
            {shownMembers.map((id) => {
              const session = byId.get(id);
              return session ? projectCell(session, "folder") : null;
            })}
          </div>
        </HomeOverlay>

        <HomeOverlay
          open={archiveOpen}
          title="已归档"
          subtitle="不在主屏上；取消归档后回到主屏末尾"
          footer="点击项目查看详情、取消归档或删除"
          panelRef={archivePanelRef}
          leaving={false}
          rightInset={sidebarInset}
          onBackdrop={() => setArchiveOpen(false)}
        >
          <div className="grid grid-cols-[repeat(auto-fill,96px)] content-start justify-center gap-x-2.5 gap-y-[18px] p-1">
            {archived.map((session) => projectCell(session, "archive"))}
          </div>
        </HomeOverlay>

        <HomeSidebar open={editing} subject={subject} subjectKey={subjectKey} onClose={exitEdit} />
      </div>

      {gestures.drag && ghostFace && (
        <div
          ref={ghostRef}
          className="home-ghost"
          data-landing={gestures.landing ? "true" : undefined}
          style={{ transform: gestures.drag.start }}
        >
          <div className="flex w-24 flex-col items-center pt-1">
            <div className="home-ghost-lift relative h-16 w-16">
              <TileFaceView face={ghostFace} />
              <CornerBadge mark={dragGhostMark} />
            </div>
          </div>
        </div>
      )}

      {toast && (
        <div
          key={toast.id}
          role="status"
          aria-live="polite"
          className="home-toast pointer-events-none fixed bottom-7 left-1/2 z-[80] -translate-x-1/2 whitespace-nowrap rounded-[10px] bg-ink-strong px-3.5 py-[9px] text-[12.5px] text-surface shadow-[0_10px_30px_rgb(var(--shadow)/0.25)]"
        >
          {toast.text}
        </div>
      )}

      <GlobalSettingsModal
        open={settingsOpen}
        state={globalState}
        onClose={() => setSettingsOpen(false)}
        onChanged={(next) => {
          onGlobalStateChanged(next);
          void refresh();
        }}
      />
    </div>
  );
}
