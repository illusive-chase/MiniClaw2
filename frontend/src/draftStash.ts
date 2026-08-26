/* Unsaved virtual-node drafts, stashed in the browser.
 *
 * The details panel keeps one local draft per selected virtual and only writes
 * it to the server when asked. That leaves several ways for typing to vanish:
 * selecting another node resets the draft, the panel unmounts when the
 * selection changes kind, and a reload keeps nothing. A periodic push to the
 * server closes most of that gap, but not all of it — the window before the
 * next tick, and the lanes where pushing is unsafe because saving a prompt on
 * an active auto planspace promotes the node and launches the agent.
 *
 * So the stash is the floor: whatever is on screen survives locally even when
 * it must not be sent. Entries hold both the local draft and the persisted
 * draft it was based on; that baseline is what lets a restore reconcile
 * against a node someone else moved meanwhile instead of overwriting it.
 *
 * Storage can be unavailable (private windows, disabled site data) or throw on
 * access. Every entry point degrades to "no stash" rather than propagating,
 * because losing the safety net must never take the editor down with it. */

const STORAGE_KEY = "miniclaw.virtualDraftStash";

/* Bounds on a store the user never sees and cannot clean up by hand. Both are
 * generous next to how many virtuals one editing session touches. */
const MAX_ENTRIES = 40;
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

export type StashedDraftRecord = {
  /** Epoch ms the entry was written; drives pruning and the restore notice. */
  savedAt: number;
  /** The persisted draft this edit was based on. */
  baseline: unknown;
  /** The local draft as it stood on screen. */
  draft: unknown;
};

export type StashMap = Record<string, StashedDraftRecord>;

export function draftStashKey(sessionId: string, nodeId: string): string {
  return `${sessionId}:${nodeId}`;
}

/* Entries are dropped oldest-first, so an in-progress edit outlives the stale
 * ones it shares the budget with. A record with no usable timestamp is dropped
 * outright rather than inheriting a default age: as `0` it would read as fresh
 * under any small clock and could hold a slot indefinitely. */
export function pruneDraftStash(
  entries: StashMap,
  now: number,
  { maxEntries = MAX_ENTRIES, maxAgeMs = MAX_AGE_MS } = {},
): StashMap {
  const fresh = Object.entries(entries).filter(([, record]) => {
    const savedAt = record?.savedAt;
    if (typeof savedAt !== "number" || !Number.isFinite(savedAt)) return false;
    return now - savedAt <= maxAgeMs;
  });
  fresh.sort((a, b) => (b[1].savedAt ?? 0) - (a[1].savedAt ?? 0));
  return Object.fromEntries(fresh.slice(0, maxEntries));
}

function readStashMap(): StashMap {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return {};
    }
    return parsed as StashMap;
  } catch {
    /* localStorage unavailable or the payload is not ours; treat as empty */
    return {};
  }
}

function writeStashMap(entries: StashMap): boolean {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
    return true;
  } catch {
    /* localStorage unavailable or over quota; the in-memory draft still stands */
    return false;
  }
}

export function readStashedDraft(
  key: string,
  now = Date.now(),
): StashedDraftRecord | null {
  const entries = readStashMap();
  const record = entries[key];
  if (!record || typeof record !== "object") return null;
  if (!("draft" in record) || !("baseline" in record)) return null;
  if (
    typeof record.savedAt !== "number" ||
    !Number.isFinite(record.savedAt) ||
    now - record.savedAt > MAX_AGE_MS
  ) {
    delete entries[key];
    writeStashMap(entries);
    return null;
  }
  return record;
}

export function writeStashedDraft(
  key: string,
  record: StashedDraftRecord,
): boolean {
  const entries = readStashMap();
  entries[key] = record;
  return writeStashMap(pruneDraftStash(entries, record.savedAt));
}

export function clearStashedDraft(key: string): void {
  const entries = readStashMap();
  if (!(key in entries)) return;
  delete entries[key];
  writeStashMap(entries);
}

/** What to do with a stashed draft when its node is selected again.
 *
 * - `drop`: the stash says nothing the saved node does not already say.
 * - `adopt`: the node has not moved since the stash was written, so the local
 *   draft is restored verbatim.
 * - `merge`: the node moved elsewhere, so the two have to be reconciled field
 *   by field and any overwritten edit reported. */
export type StashRestoreDecision = "drop" | "adopt" | "merge";

export function stashRestoreDecision(args: {
  stashedSignature: string;
  baselineSignature: string;
  persistedSignature: string;
}): StashRestoreDecision {
  if (args.stashedSignature === args.persistedSignature) return "drop";
  if (args.baselineSignature === args.persistedSignature) return "adopt";
  return "merge";
}

/* A stash written by an older build can disagree with today's draft shape, and
 * a draft with the wrong fields would be sent to the server on the next save.
 * Comparing key sets and value kinds against a freshly derived draft is enough
 * to catch realistic schema drift without hand-writing a validator that has to
 * be kept in step with the draft type. */
export function draftShapeMatches(
  candidate: unknown,
  template: Record<string, unknown>,
): boolean {
  if (
    typeof candidate !== "object" ||
    candidate === null ||
    Array.isArray(candidate)
  ) {
    return false;
  }
  const record = candidate as Record<string, unknown>;
  const templateKeys = Object.keys(template);
  if (templateKeys.length !== Object.keys(record).length) return false;
  for (const key of templateKeys) {
    if (!Object.prototype.hasOwnProperty.call(record, key)) return false;
    const expected = template[key];
    const actual = record[key];
    if (typeof expected !== typeof actual) return false;
    if ((expected === null) !== (actual === null)) return false;
    if (Array.isArray(expected) !== Array.isArray(actual)) return false;
  }
  return true;
}

/** Whether this tick should push the draft to the server.
 *
 * Pushing is skipped while a write is already in flight, while the form is
 * incomplete (an empty prompt is someone still typing, not a failure worth
 * reporting), and when the draft has not changed since the last attempt — that
 * last guard is what keeps a failing write from retrying every tick. */
export function shouldAutosaveDraft(args: {
  enabled: boolean;
  dirty: boolean;
  saving: boolean;
  incomplete: boolean;
  signature: string;
  lastAttemptSignature: string | null;
}): boolean {
  if (!args.enabled || !args.dirty || args.saving || args.incomplete) {
    return false;
  }
  return args.signature !== args.lastAttemptSignature;
}
