import { useCallback, useEffect, useRef, useState } from "react";

import { applySelfUpdate, checkSelfUpdate, getSelfUpdate } from "./api";
import type { SelfUpdateApplyResult, SelfUpdateState } from "./types";

export const SELF_UPDATE_POLL_MS = 10 * 60_000;
export const DISMISSED_UPDATE_STORAGE_KEY = "miniclaw.selfUpdate.dismissed";

export function targetSha(state: SelfUpdateState | null): string | null {
  if (!state?.available || state.commits.length === 0) return null;
  return state.commits[state.commits.length - 1]?.sha ?? null;
}

export function readDismissedUpdate(): string | null {
  try {
    return window.localStorage.getItem(DISMISSED_UPDATE_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function writeDismissedUpdate(sha: string): void {
  try {
    window.localStorage.setItem(DISMISSED_UPDATE_STORAGE_KEY, sha);
  } catch {
    /* The in-memory dismissal still works for this page load. */
  }
}

export function canApplyUpdate(state: SelfUpdateState | null): boolean {
  return Boolean(
    state?.available &&
      state.fast_forward &&
      !state.dirty &&
      state.ahead === 0 &&
      state.blockers.length === 0,
  );
}

export function useSelfUpdate() {
  const [state, setState] = useState<SelfUpdateState | null>(null);
  const [dismissedSha, setDismissedSha] = useState(readDismissedUpdate);
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const next = await getSelfUpdate();
      if (mounted.current) setState(next);
    } catch {
      /* Ambient polling keeps the last useful state. */
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, SELF_UPDATE_POLL_MS);
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    return () => {
      mounted.current = false;
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [refresh]);

  useEffect(() => {
    if (!state?.checking) return;
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [refresh, state?.checking]);

  const check = useCallback(async () => {
    setChecking(true);
    setError(null);
    try {
      const next = await checkSelfUpdate();
      setState(next);
      return next;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      throw err;
    } finally {
      setChecking(false);
    }
  }, []);

  const apply = useCallback(async (): Promise<SelfUpdateApplyResult> => {
    setApplying(true);
    setError(null);
    try {
      return await applySelfUpdate();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      await refresh();
      throw err;
    } finally {
      setApplying(false);
    }
  }, [refresh]);

  const dismiss = useCallback(() => {
    const sha = targetSha(state);
    if (!sha) return;
    setDismissedSha(sha);
    writeDismissedUpdate(sha);
  }, [state]);

  return {
    state,
    checking,
    applying,
    error,
    visible: Boolean(state?.available && targetSha(state) !== dismissedSha),
    refresh,
    check,
    apply,
    dismiss,
  };
}
