/* Which lane the user is currently looking at, per project.
 *
 * This is view state, not execution state. It decides where the `+` lands,
 * which lane the canvas draws as current, and which lane a double-click
 * creates into — and nothing else. The backend never sees it.
 *
 * It exists because `active_planspace_id` was doing this job while also
 * gating Promote and arming auto lanes. Those are execution concerns that
 * happen to need a lane; this one is purely "what am I looking at". Splitting
 * them lets focus follow the user's clicks freely, which a field the backend
 * reads for scheduling could never safely do.
 *
 * Same read/write shape as `projectSort.ts` and `libraryTreeState.ts`: total
 * functions that fall back rather than throw, because localStorage can be
 * unavailable (private windows, disabled storage) and the canvas must still
 * render.
 */

export const FOCUSED_LANE_STORAGE_KEY = "miniclaw.focusedLane";

/** `projectId` → `planspaceId`. One key for every project, matching the
 * single-key style of the other persisted view state, so the storage does not
 * accumulate one entry per project the user ever opened. */
export type FocusedLaneMap = Record<string, string>;

/** Tolerates anything: a stored `null`, an array, non-string values, empty
 * strings. Unknown project ids are kept — a project absent from this session
 * may be present in the next one, and dropping it would silently forget the
 * user's focus there. */
export function normalizeFocusedLanes(value: unknown): FocusedLaneMap {
  const out: FocusedLaneMap = {};
  if (!value || typeof value !== "object" || Array.isArray(value)) return out;
  for (const [projectId, laneId] of Object.entries(
    value as Record<string, unknown>,
  )) {
    if (!projectId) continue;
    if (typeof laneId === "string" && laneId) out[projectId] = laneId;
  }
  return out;
}

export function readFocusedLanes(): FocusedLaneMap {
  try {
    const raw = window.localStorage.getItem(FOCUSED_LANE_STORAGE_KEY);
    if (raw) return normalizeFocusedLanes(JSON.parse(raw) as unknown);
  } catch {
    /* unreadable or unparseable — fall through to empty */
  }
  return {};
}

export function writeFocusedLanes(map: FocusedLaneMap): void {
  try {
    window.localStorage.setItem(
      FOCUSED_LANE_STORAGE_KEY,
      JSON.stringify(normalizeFocusedLanes(map)),
    );
  } catch {
    /* localStorage unavailable; the caller's in-memory state stays usable */
  }
}

/** The remembered lane for one project, or null if none was stored. */
export function readFocusedLane(projectId: string | null | undefined): string | null {
  if (!projectId) return null;
  return readFocusedLanes()[projectId] ?? null;
}

/** Remember (or forget, with `null`) the focused lane for one project.
 * A read-modify-write, so a write from one tab does not wipe the other
 * projects another tab recorded. */
export function writeFocusedLane(
  projectId: string | null | undefined,
  planspaceId: string | null,
): void {
  if (!projectId) return;
  const next = readFocusedLanes();
  if (planspaceId) next[projectId] = planspaceId;
  else delete next[projectId];
  writeFocusedLanes(next);
}

export type FocusedLaneResolution = {
  /** What localStorage remembered for this project, if anything. */
  stored: string | null;
  /** `active_planspace_id` from the contextspace. A compatibility input only:
   * Phase 3 deletes the field, and this argument goes with it. Everything
   * below it must therefore already be a correct standalone answer. */
  active: string | null;
  /** Lanes that exist and are not hidden. The only lanes focus may land on:
   * a hidden lane draws no nodes, so focusing it would put the `+` and the
   * double-click target somewhere the user cannot see. */
  visible: readonly string[];
  /** Visible lanes ordered most-recently-active first. Empty is normal — a
   * fresh project has lanes but no runs. */
  recentlyActive: readonly string[];
};

/** Pick the lane to focus, in descending order of how much it reflects an
 * actual choice by the user.
 *
 * The `active` step is scaffolding: while the backend field still exists it
 * keeps a returning user on the lane they last executed in. Every step after
 * it must stand on its own, because Phase 3 removes that step and what is
 * left here becomes the whole answer.
 */
export function resolveFocusedLane(args: FocusedLaneResolution): string | null {
  const visible = new Set(args.visible.filter(Boolean));
  if (visible.size === 0) return null;

  if (args.stored && visible.has(args.stored)) return args.stored;
  if (args.active && visible.has(args.active)) return args.active;

  for (const laneId of args.recentlyActive) {
    if (visible.has(laneId)) return laneId;
  }

  /* A project whose lanes are all empty has no activity to rank, and
   * returning null there would leave the canvas with no `+` at all and no way
   * to create the first node. Falling back to the first visible lane keeps
   * that project usable; lane order is stable, so the choice is at least
   * predictable. */
  for (const laneId of args.visible) {
    if (visible.has(laneId)) return laneId;
  }
  return null;
}
