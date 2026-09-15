import { useEffect, useState } from "react";
import { getMigrationStatus, type SyncProgress } from "./api";

export function useSyncProgress(active: boolean) {
  const [progress, setProgress] = useState<SyncProgress | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  useEffect(() => {
    setProgress(null);
    setElapsedSeconds(0);
    if (!active) return;
    let startedAt = Date.now();
    const controller = new AbortController();
    let disposed = false;
    let pending = false;
    const poll = async () => {
      if (pending || disposed) return;
      pending = true;
      try {
        const status = await getMigrationStatus(controller.signal);
        if (!disposed && status.sync_progress && status.sync_progress.phase !== "idle") {
          if (status.sync_progress.started_at !== null) {
            startedAt = status.sync_progress.started_at * 1000;
            setElapsedSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));
          }
          setProgress(status.sync_progress);
        }
      } catch {
        return;
      } finally {
        pending = false;
      }
    };
    void poll();
    const interval = window.setInterval(() => {
      setElapsedSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));
      void poll();
    }, 1500);
    return () => {
      disposed = true;
      window.clearInterval(interval);
      controller.abort();
    };
  }, [active]);

  return { progress, elapsedSeconds };
}
