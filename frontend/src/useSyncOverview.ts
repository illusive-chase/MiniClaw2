import { useCallback, useEffect, useRef, useState } from "react";

import { getSyncOverview } from "./api";
import type { SyncOverview } from "./types";

/**
 * The sync overview, re-read when the backend says a category changed, when
 * the tab regains focus, and on demand. It never polls: the backend pushes
 * `sync_overview_changed`, and the read itself touches no remote.
 */
export function useSyncOverview(enabled: boolean) {
  const [overview, setOverview] = useState<SyncOverview | null>(null);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  const refresh = useCallback(async () => {
    if (!enabledRef.current) return;
    try {
      setOverview(await getSyncOverview());
    } catch {
      /* ambient status: keep the last snapshot */
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [enabled, refresh]);

  return { overview, setOverview, refresh };
}
