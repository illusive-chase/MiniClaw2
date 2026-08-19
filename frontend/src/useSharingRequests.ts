import { useCallback, useEffect, useRef, useState } from "react";
import {
  acceptSharingRequest,
  cancelSharingRequest,
  listSharingRequests,
  rejectSharingRequest,
  syncNow,
} from "./api";
import type {
  GlobalState,
  SessionInfo,
  SharingRequestInfo,
  SharingRequestResult,
} from "./types";

/** What a completed local mutation is still waiting to publish.
 *
 * Local persistence and the remote exchange are two independently retryable
 * phases. A failed sync never undoes the local write — the host's migration in
 * particular is one-way — so the UI has to be able to say "done here, not yet
 * synced" instead of reporting the whole action as failed. */
export type PendingSync = {
  action: "accept" | "reject" | "cancel";
  sessionId: string;
  error: string;
};

const SYNC_FAILURE_MESSAGES: Record<PendingSync["action"], string> = {
  accept: "共享已在本机开启，尚未同步到远端。",
  reject: "拒绝已保存在本机，尚未同步到远端。",
  cancel: "取消已保存在本机，尚未同步到远端。",
};

export function pendingSyncMessage(pending: PendingSync): string {
  return `${SYNC_FAILURE_MESSAGES[pending.action]} ${pending.error}`;
}

function errorText(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

type Options = {
  /** Called with the fresh GlobalState after any successful sync. */
  onGlobalState?: (state: GlobalState) => void;
  /** Called with the session a mutation returned. */
  onSession?: (session: SessionInfo) => void;
  /** Called after any successful sync. A sync can turn the project shared on
   * the host's side, which is how the requester learns it may now bind a local
   * checkout — session state has to be re-read, not inferred from the request. */
  onSynced?: () => void;
  /** Poll cadence; requests only change on a sync, so this stays slow. */
  intervalMs?: number;
};

/** Owns the sharing-request list and the local-then-sync mutation flow. */
export function useSharingRequests(enabled: boolean, options: Options = {}) {
  const { onGlobalState, onSession, onSynced, intervalMs = 30_000 } = options;
  const [requests, setRequests] = useState<SharingRequestInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingSync, setPendingSync] = useState<PendingSync | null>(null);
  const callbacks = useRef({ onGlobalState, onSession, onSynced });
  callbacks.current = { onGlobalState, onSession, onSynced };

  const refresh = useCallback(async () => {
    try {
      setRequests(await listSharingRequests());
    } catch (err) {
      setError(errorText(err));
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const interval = window.setInterval(() => void refresh(), intervalMs);
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
    };
  }, [enabled, intervalMs, refresh]);

  /** Phase two: publish, then re-read what the remote taught us. */
  const publish = useCallback(
    async (action: PendingSync["action"], sessionId: string): Promise<boolean> => {
      try {
        const state = await syncNow();
        callbacks.current.onGlobalState?.(state);
        setPendingSync(null);
        await refresh();
        callbacks.current.onSynced?.();
        return true;
      } catch (err) {
        setPendingSync({ action, sessionId, error: errorText(err) });
        return false;
      }
    },
    [refresh],
  );

  const run = useCallback(
    async (
      action: PendingSync["action"],
      sessionId: string,
      mutate: () => Promise<SharingRequestResult>,
    ): Promise<SessionInfo | null> => {
      setBusy(true);
      setError(null);
      try {
        const { request: updated, session } = await mutate();
        callbacks.current.onSession?.(session);
        setRequests((current) => [
          ...current.filter((item) => item.id !== updated.id),
          updated,
        ]);
        await publish(action, sessionId);
        return session;
      } catch (err) {
        setError(errorText(err));
        await refresh();
        return null;
      } finally {
        setBusy(false);
      }
    },
    [publish, refresh],
  );

  const accept = useCallback(
    (sessionId: string, requestId: string) =>
      run("accept", sessionId, () => acceptSharingRequest(sessionId, requestId)),
    [run],
  );

  const reject = useCallback(
    (sessionId: string, requestId: string) =>
      run("reject", sessionId, () => rejectSharingRequest(sessionId, requestId)),
    [run],
  );

  const cancel = useCallback(
    (sessionId: string, requestId: string) =>
      run("cancel", sessionId, () => cancelSharingRequest(sessionId, requestId)),
    [run],
  );

  /** Retry only the exchange. The local record already exists, so re-running
   * the mutation would either be a no-op or, for accept, be refused. */
  const retrySync = useCallback(async () => {
    if (!pendingSync) return;
    setBusy(true);
    try {
      await publish(pendingSync.action, pendingSync.sessionId);
    } finally {
      setBusy(false);
    }
  }, [pendingSync, publish]);

  /** Pull remote state without changing anything — "同步检查状态". */
  const checkForUpdates = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const state = await syncNow();
      callbacks.current.onGlobalState?.(state);
      setPendingSync(null);
      await refresh();
      callbacks.current.onSynced?.();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  return {
    requests,
    busy,
    error,
    pendingSync,
    clearError: useCallback(() => setError(null), []),
    refresh,
    accept,
    reject,
    cancel,
    retrySync,
    checkForUpdates,
  };
}
