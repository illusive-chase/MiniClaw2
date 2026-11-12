/* Home-screen arrangement as pure functions (design §4.2–§4.3).
 *
 * Every edit the home screen makes — drag reorder, merge into a folder, pull
 * out, dissolve, rename — is a function from one `HomeItem[]` to the next. No
 * DOM, no fetch: that keeps them testable, and lets a 409 from `PUT /home` be
 * resolved by replaying the same edit on the freshly fetched layout.
 */
import type { HomeFolder, HomeItem } from "./types";

export const NEW_FOLDER_NAME = "新建组";
export const UNNAMED_FOLDER = "未命名组";
export const MAX_FOLDER_NAME_LENGTH = 40;

/** Stable key for a home slot, shared by React keys, drag state, and FLIP. */
export type SlotKey = `p:${string}` | `f:${string}`;

export function slotKey(item: HomeItem): SlotKey {
  return "project" in item ? `p:${item.project}` : `f:${item.folder.id}`;
}

export function isFolder(item: HomeItem): item is { folder: HomeFolder } {
  return "folder" in item;
}

export function projectIds(items: readonly HomeItem[]): string[] {
  return items.flatMap((item) => ("project" in item ? [item.project] : item.folder.projects));
}

export function folderOf(items: readonly HomeItem[], projectId: string): HomeFolder | null {
  for (const item of items) {
    if (isFolder(item) && item.folder.projects.includes(projectId)) return item.folder;
  }
  return null;
}

export function findFolder(items: readonly HomeItem[], folderId: string): HomeFolder | null {
  for (const item of items) {
    if (isFolder(item) && item.folder.id === folderId) return item.folder;
  }
  return null;
}

export function indexOfSlot(items: readonly HomeItem[], key: SlotKey): number {
  return items.findIndex((item) => slotKey(item) === key);
}

export function newFolderId(existing: readonly HomeItem[]): string {
  const taken = new Set(existing.filter(isFolder).map((item) => item.folder.id));
  for (;;) {
    const bytes = new Uint8Array(4);
    crypto.getRandomValues(bytes);
    const id = "g_" + [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    if (!taken.has(id)) return id;
  }
}

function moveWithin<T>(list: readonly T[], from: number, to: number): T[] {
  const next = [...list];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

/** Move a top-level slot to `to` (clamped). A no-op returns the same array. */
export function moveSlot(items: readonly HomeItem[], key: SlotKey, to: number): HomeItem[] {
  const from = indexOfSlot(items, key);
  const target = Math.max(0, Math.min(items.length - 1, to));
  if (from < 0 || from === target) return items as HomeItem[];
  return moveWithin(items, from, target);
}

/** Reorder a project inside its folder. */
export function moveInFolder(
  items: readonly HomeItem[],
  folderId: string,
  projectId: string,
  to: number,
): HomeItem[] {
  return items.map((item) => {
    if (!isFolder(item) || item.folder.id !== folderId) return item;
    const from = item.folder.projects.indexOf(projectId);
    const target = Math.max(0, Math.min(item.folder.projects.length - 1, to));
    if (from < 0 || from === target) return item;
    return { folder: { ...item.folder, projects: moveWithin(item.folder.projects, from, target) } };
  });
}

/** Drop `projectId` onto a top-level slot: append to a folder, or pair two
 * projects into a new folder in the target's place. Folders never nest, so a
 * folder cannot be the dragged item. */
export function mergeInto(
  items: readonly HomeItem[],
  targetKey: SlotKey,
  projectId: string,
  folderId: string = newFolderId(items),
): { items: HomeItem[]; folderId: string; created: boolean } | null {
  if (targetKey === `p:${projectId}`) return null;
  const without = items.filter((item) => !("project" in item && item.project === projectId));
  const index = indexOfSlot(without, targetKey);
  if (index < 0) return null;
  const target = without[index];
  const next = [...without];
  if (isFolder(target)) {
    next[index] = { folder: { ...target.folder, projects: [...target.folder.projects, projectId] } };
    return { items: next, folderId: target.folder.id, created: false };
  }
  next[index] = { folder: { id: folderId, name: NEW_FOLDER_NAME, projects: [target.project, projectId] } };
  return { items: next, folderId, created: true };
}

/** Take a project out of its folder and place it right after that folder. The
 * folder disappears when its last member leaves. */
export function pullOut(items: readonly HomeItem[], projectId: string): HomeItem[] {
  const next: HomeItem[] = [];
  for (const item of items) {
    if (!isFolder(item) || !item.folder.projects.includes(projectId)) {
      next.push(item);
      continue;
    }
    const rest = item.folder.projects.filter((id) => id !== projectId);
    if (rest.length) next.push({ folder: { ...item.folder, projects: rest } });
    next.push({ project: projectId });
  }
  return next;
}

/** Members return to the folder's place, in folder order. */
export function dissolve(items: readonly HomeItem[], folderId: string): HomeItem[] {
  return items.flatMap((item) =>
    isFolder(item) && item.folder.id === folderId
      ? item.folder.projects.map((project) => ({ project }))
      : [item],
  );
}

/** Move a project into an existing folder (sidebar "所在组"), from anywhere. */
export function moveToFolder(items: readonly HomeItem[], projectId: string, folderId: string): HomeItem[] {
  const current = folderOf(items, projectId);
  if (current?.id === folderId) return items as HomeItem[];
  const detached = current ? pullOut(items, projectId) : (items as HomeItem[]);
  const merged = mergeInto(detached, `f:${folderId}`, projectId);
  return merged ? merged.items : (items as HomeItem[]);
}

/** Wrap a project in a brand-new single-member folder, in place. */
export function wrapInNewFolder(
  items: readonly HomeItem[],
  projectId: string,
  folderId: string = newFolderId(items),
): HomeItem[] {
  const detached = folderOf(items, projectId) ? pullOut(items, projectId) : (items as HomeItem[]);
  return detached.map((item) =>
    "project" in item && item.project === projectId
      ? { folder: { id: folderId, name: NEW_FOLDER_NAME, projects: [projectId] } }
      : item,
  );
}

/** Rename keeps whatever the user typed; trimming and the empty-name fallback
 * happen on commit (`commitFolderName`) so the input does not fight the caret. */
export function renameFolder(items: readonly HomeItem[], folderId: string, name: string): HomeItem[] {
  return items.map((item) =>
    isFolder(item) && item.folder.id === folderId ? { folder: { ...item.folder, name } } : item,
  );
}

export function commitFolderName(name: string): string {
  const trimmed = name.trim().slice(0, MAX_FOLDER_NAME_LENGTH);
  return trimmed || UNNAMED_FOLDER;
}

/** Client-side mirror of the server's read-time convergence: drop ids that are
 * gone, then append unlisted projects in the given order. Used for optimistic
 * updates after create/archive so the grid never waits for a refetch. */
export function reconcile(items: readonly HomeItem[], activeIds: readonly string[]): HomeItem[] {
  const active = new Set(activeIds);
  const next: HomeItem[] = [];
  for (const item of items) {
    if ("project" in item) {
      if (active.has(item.project)) next.push(item);
      continue;
    }
    const kept = item.folder.projects.filter((id) => active.has(id));
    if (kept.length) next.push({ folder: { ...item.folder, projects: kept } });
  }
  const listed = new Set(projectIds(next));
  for (const id of activeIds) {
    if (!listed.has(id)) {
      next.push({ project: id });
      listed.add(id);
    }
  }
  return next;
}

/** Make every folder name storable before a `PUT`. */
export function normalizeForSave(items: readonly HomeItem[]): HomeItem[] {
  return items.map((item) =>
    isFolder(item) ? { folder: { ...item.folder, name: commitFolderName(item.folder.name) } } : item,
  );
}

export type SlotBadges = {
  /** Nodes blocked on a human. */
  waiting: number;
  /** Unread error nodes. */
  errors: number;
  running: number;
};

export const NO_BADGES: SlotBadges = { waiting: 0, errors: 0, running: 0 };

/** A folder's badge sums its members. */
export function folderBadges(
  folder: HomeFolder,
  byProject: ReadonlyMap<string, SlotBadges>,
): SlotBadges {
  const total = { waiting: 0, errors: 0, running: 0 };
  for (const id of folder.projects) {
    const badges = byProject.get(id);
    if (!badges) continue;
    total.waiting += badges.waiting;
    total.errors += badges.errors;
    total.running += badges.running;
  }
  return total;
}
