/* Local-first home arrangement with a single-writer sync loop (design §5.2).
 *
 * Every edit lands in local state at once; a debounced loop then sends the
 * whole arrangement with the revision it was based on. A 409 means another
 * edit reached the server first: the loop refetches, replays every edit not
 * yet acknowledged on top of the fresh layout, and tries again. The edits are
 * pure functions (`homeLayout.ts`), which is what makes the replay possible.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, getHome, putHome } from "../../api";
import { normalizeForSave, reconcile } from "../../homeLayout";
import type { HomeItem } from "../../types";

export type HomeEdit = (items: HomeItem[]) => HomeItem[];

const FLUSH_DELAY_MS = 250;
const MAX_REBASES = 3;

export function useHomeSync(activeIds: readonly string[]) {
  const [raw, setRaw] = useState<HomeItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const revision = useRef<string | null>(null);
  const pending = useRef<HomeEdit[]>([]);
  const inflight = useRef(false);
  const held = useRef(false);
  const refreshWanted = useRef(false);
  const timer = useRef<number | null>(null);
  const idsRef = useRef(activeIds);
  idsRef.current = activeIds;

  /* What the grid draws: the stored order, converged onto the projects this
   * page currently knows about, so a project created or archived a moment ago
   * is already in place before any refetch. */
  const items = useMemo(() => (raw === null ? null : reconcile(raw, activeIds)), [raw, activeIds]);
  const itemsRef = useRef(items);
  itemsRef.current = items;

  const flushRef = useRef<() => void>(() => {});

  const schedule = useCallback((delay = FLUSH_DELAY_MS) => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      flushRef.current();
    }, delay);
  }, []);

  const load = useCallback(async () => {
    const fresh = await getHome();
    revision.current = fresh.revision;
    setRaw(fresh.items);
    setError(null);
  }, []);

  const refresh = useCallback(async () => {
    if (held.current || inflight.current || pending.current.length) {
      refreshWanted.current = true;
      return;
    }
    try {
      await load();
    } catch (err) {
      setError(String(err));
    }
  }, [load]);

  flushRef.current = async () => {
    if (inflight.current || held.current) return;
    if (!pending.current.length) {
      if (refreshWanted.current) {
        refreshWanted.current = false;
        void refresh();
      }
      return;
    }
    const current = itemsRef.current;
    if (current === null || revision.current === null) return;
    inflight.current = true;
    const sent = pending.current.length;
    let rebases = 0;
    let body = normalizeForSave(current);
    try {
      for (;;) {
        try {
          const saved = await putHome(body, revision.current!);
          revision.current = saved.revision;
          pending.current.splice(0, sent);
          setError(null);
          break;
        } catch (err) {
          if (!(err instanceof ApiError) || err.status !== 409 || rebases >= MAX_REBASES) throw err;
          rebases += 1;
          const fresh = await getHome();
          revision.current = fresh.revision;
          const base = reconcile(fresh.items, idsRef.current);
          const replayed = pending.current.reduce((acc, edit) => edit(acc), base);
          setRaw(replayed);
          body = normalizeForSave(replayed);
        }
      }
    } catch (err) {
      /* Not a race but a rejected layout (or the server is unreachable):
       * the local edits cannot be kept, so fall back to the stored one. */
      pending.current = [];
      setError(err instanceof ApiError ? err.detail ?? err.message : String(err));
      try {
        await load();
      } catch {
        /* the error banner already explains */
      }
    } finally {
      inflight.current = false;
    }
    if (pending.current.length) schedule(0);
    else if (refreshWanted.current) {
      refreshWanted.current = false;
      void refresh();
    }
  };

  /** Apply an edit locally and queue it for the server. Pass `applied` when the
   * caller already put the result on screen (a drag that edited live). */
  const apply = useCallback((edit: HomeEdit, options: { applied?: boolean; immediate?: boolean } = {}) => {
    if (!options.applied) {
      const current = itemsRef.current;
      if (current === null) return;
      const next = edit(current);
      if (next === current) return;
      itemsRef.current = next;
      setRaw(next);
    }
    pending.current.push(edit);
    schedule(options.immediate ? 0 : FLUSH_DELAY_MS);
  }, [schedule]);

  /** Change what is drawn without queueing a write (live drag preview). */
  const preview = useCallback((next: HomeItem[]) => {
    itemsRef.current = next;
    setRaw(next);
  }, []);

  /** While held, nothing is sent and refetches wait: a drag owns the grid. */
  const hold = useCallback((value: boolean) => {
    held.current = value;
    if (!value) schedule(0);
  }, [schedule]);

  useEffect(() => {
    void load().catch((err) => setError(String(err)));
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [load]);

  return { items, error, setError, apply, preview, hold, refresh };
}
