/* Pure decision logic for editing an existing tag's name.
 *
 * The rename control itself lives in `TagEditPopover`, which portals to
 * `document.body` and so cannot be rendered by `renderToStaticMarkup` — the
 * repo has no DOM harness. Keeping these two rules out of the component is
 * what makes them testable, and they are the parts worth locking down: both
 * mirror a backend rule in `tags.py`, and getting either wrong produces a
 * request the server rejects (a 400 surfaced in the panel) or a silent no-op.
 */

import type { Tag } from "./types";

/**
 * Whether `draft` collides with another tag's name.
 *
 * Case- and whitespace-insensitive, matching `_require_unique_name`, which
 * compares `casefold()`ed names. `tagId` is excluded so re-casing a tag's own
 * name ("work" → "Work") is not reported as a conflict against itself.
 */
export function renameConflicts(
  tags: readonly Tag[],
  tagId: string,
  draft: string,
): boolean {
  const folded = draft.trim().toLowerCase();
  if (!folded) return false;
  return tags.some(
    (tag) => tag.id !== tagId && tag.name.trim().toLowerCase() === folded,
  );
}

/**
 * Whether committing `draft` should issue a PATCH.
 *
 * An empty name is rejected by the backend (`name must not be empty`), and a
 * name equal to the current one after trimming would be a write with no
 * effect. Both close the editor instead, so blurring an untouched field is
 * silent rather than a round-trip or an error.
 */
export function shouldCommitRename(currentName: string, draft: string): boolean {
  const next = draft.trim();
  return next.length > 0 && next !== currentName.trim();
}
