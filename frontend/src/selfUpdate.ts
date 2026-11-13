import type { SelfUpdateState } from "./types";

export function canApplyUpdate(state: SelfUpdateState | null): boolean {
  return Boolean(
    state?.available &&
      state.fast_forward &&
      !state.dirty &&
      state.ahead === 0 &&
      state.blockers.length === 0,
  );
}

const PENDING_KEY = "miniclaw2.selfUpdate.pending";
/** A pending record older than this is a leftover, not an update in flight. */
const PENDING_TTL_MS = 15 * 60 * 1000;

export type PendingUpdate = {
  target: string;
  oldHead: string | null;
  /** The backend incarnation that accepted the request; any other one is the replacement. */
  bootId: string;
  startedAt: number;
  /** Set once the page has reloaded for this update, so it never loops. */
  reloaded?: boolean;
};

function readPending(): PendingUpdate | null {
  try {
    const raw = sessionStorage.getItem(PENDING_KEY);
    const value = raw ? (JSON.parse(raw) as PendingUpdate) : null;
    return value && Date.now() - value.startedAt < PENDING_TTL_MS ? value : null;
  } catch {
    return null;
  }
}

/** Remembered across the reload so the next page can say how it went. */
export function rememberPendingUpdate(update: PendingUpdate): void {
  try {
    sessionStorage.setItem(PENDING_KEY, JSON.stringify(update));
  } catch {
    /* best effort */
  }
}

export function takePendingUpdate(): PendingUpdate | null {
  const value = readPending();
  try {
    sessionStorage.removeItem(PENDING_KEY);
  } catch {
    /* best effort */
  }
  return value;
}

export function forgetPendingUpdate(): void {
  try {
    sessionStorage.removeItem(PENDING_KEY);
  } catch {
    /* best effort */
  }
}

/**
 * Whether an auth prompt should reload the page instead of showing the login.
 *
 * Passcode sessions live in backend memory, so the replacement backend
 * rejects the old cookie. Showing the login in place would keep the old
 * frontend bundle running against the new backend; reloading first logs in
 * on the new one. True at most once per update.
 */
export function shouldReloadForUpdate(): boolean {
  const pending = readPending();
  if (!pending || pending.reloaded) return false;
  rememberPendingUpdate({ ...pending, reloaded: true });
  return true;
}
