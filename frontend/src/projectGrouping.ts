/* Landing-page project organization (design §1.6).
 *
 * Kept out of the component so the ordering rules — which are the part with real
 * edge cases — can be tested without rendering.
 */

import type { SessionInfo, Tag } from "./types";
import { NEUTRAL_TAG_COLOR } from "./tagPalette";
import type { ProjectSortMode } from "./projectSort";

/** Projects shown in the recent strip. Fixed by design §1.5. */
export const RECENT_PROJECT_COUNT = 3;

/** At or below this many projects the recent strip nearly duplicates the full
 * list, so it is hidden entirely (design §1.6). */
export const RECENT_HIDDEN_AT_OR_BELOW = 4;

export const UNTAGGED_GROUP_ID = "__untagged__";

export type ProjectGroup = {
  /** Tag id, or `UNTAGGED_GROUP_ID` for the trailing bucket. */
  id: string;
  label: string;
  color: string;
  sessions: SessionInfo[];
};

/* `last_activity_at` is derived server-side and may be absent (no nodes yet, or
 * an older backend). `created_at` always exists, so it is the fallback that
 * keeps every project comparable. */
export function activityAt(session: SessionInfo): number {
  return session.last_activity_at ?? session.created_at;
}

function byActivityDesc(a: SessionInfo, b: SessionInfo): number {
  const delta = activityAt(b) - activityAt(a);
  /* Ties broken by id so the order is stable across the 10s refresh rather than
   * reshuffling equal-timestamped cards under the cursor. */
  return delta !== 0 ? delta : a.id.localeCompare(b.id);
}

function byName(a: SessionInfo, b: SessionInfo): number {
  const an = (a.name ?? "").trim();
  const bn = (b.name ?? "").trim();
  /* Unnamed projects sort last: they render as "(unnamed)" and a run of them at
   * the top of an alphabetical list tells the user nothing. */
  if (!an !== !bn) return an ? -1 : 1;
  const cmp = an.localeCompare(bn, undefined, { numeric: true, sensitivity: "base" });
  return cmp !== 0 ? cmp : a.id.localeCompare(b.id);
}

/**
 * The recent strip: newest activity first, global.
 *
 * Deliberately computed from the unfiltered list — it is a stable shortcut, and
 * emptying it whenever a tag filter excludes the last-touched projects would
 * destroy that (design §1.6).
 */
export function recentProjects(sessions: SessionInfo[]): SessionInfo[] {
  if (sessions.length <= RECENT_HIDDEN_AT_OR_BELOW) return [];
  return [...sessions].sort(byActivityDesc).slice(0, RECENT_PROJECT_COUNT);
}

/** Multi-select AND: a project must carry every selected tag (design §1.6). */
export function filterByTags(
  sessions: SessionInfo[],
  selected: ReadonlySet<string>,
): SessionInfo[] {
  if (selected.size === 0) return sessions;
  return sessions.filter((session) => {
    const owned = new Set(session.tag_ids ?? []);
    for (const id of selected) if (!owned.has(id)) return false;
    return true;
  });
}

/** How many projects carry each tag, ignoring the active filter. */
export function tagCounts(sessions: SessionInfo[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const session of sessions) {
    for (const id of session.tag_ids ?? []) {
      counts.set(id, (counts.get(id) ?? 0) + 1);
    }
  }
  return counts;
}

export function sortFlat(
  sessions: SessionInfo[],
  mode: Exclude<ProjectSortMode, "grouped">,
): SessionInfo[] {
  return [...sessions].sort(mode === "name" ? byName : byActivityDesc);
}

/**
 * Grouped sections, in `tags.json` order, with untagged last.
 *
 * A project carrying several tags appears once in each of its sections. That is
 * intentional (design §1.6): the sections answer "what is in `work`?", and
 * showing a project only under its first tag would make that answer wrong.
 * Empty sections are dropped so an unused tag does not add a bare header.
 */
export function groupByTag(sessions: SessionInfo[], tags: Tag[]): ProjectGroup[] {
  const groups: ProjectGroup[] = [];
  for (const tag of tags) {
    const members = sessions.filter((session) => (session.tag_ids ?? []).includes(tag.id));
    if (members.length > 0) {
      groups.push({
        id: tag.id,
        label: tag.name,
        color: tag.color,
        sessions: members.sort(byActivityDesc),
      });
    }
  }
  /* An id referencing a tag this client has not loaded still counts as tagged,
   * so the project is not misfiled as untagged during the brief window before
   * /tags resolves. */
  const known = new Set(tags.map((tag) => tag.id));
  const untagged = sessions.filter(
    (session) => !(session.tag_ids ?? []).some((id) => known.has(id)),
  );
  if (untagged.length > 0) {
    groups.push({
      id: UNTAGGED_GROUP_ID,
      label: "未分类",
      color: NEUTRAL_TAG_COLOR,
      sessions: untagged.sort(byActivityDesc),
    });
  }
  return groups;
}

/** Resolve a project's tag ids to tags, in `tags.json` order. */
export function resolveTags(session: SessionInfo, byId: Map<string, Tag>): Tag[] {
  const owned = new Set(session.tag_ids ?? []);
  const resolved: Tag[] = [];
  for (const tag of byId.values()) if (owned.has(tag.id)) resolved.push(tag);
  return resolved;
}
