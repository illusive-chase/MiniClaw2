import { useEffect, useState } from "react";
import { getNodeContextBundle } from "./api";
import type { ContextBundle, NodeInfo } from "./types";

export function useNodeContextBundle(sessionId: string | undefined, node: NodeInfo | null | undefined) {
  const nodeId = node?.state !== "virtual" ? node?.id : undefined;
  const key = sessionId && nodeId ? JSON.stringify([
    sessionId, nodeId, node?.context_bundle_id, node?.context_bundle_path,
    node?.state, node?.finished_at,
  ]) : null;
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{
    key: string | null; bundle: ContextBundle | null; loading: boolean; error: string | null;
  }>({ key: null, bundle: null, loading: false, error: null });

  useEffect(() => {
    if (!sessionId || !nodeId) return;
    const controller = new AbortController();
    setState({ key, bundle: null, loading: true, error: null });
    void getNodeContextBundle(sessionId, nodeId, controller.signal).then(
      (bundle) => {
        if (!controller.signal.aborted) setState({ key, bundle, loading: false, error: null });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setState({
          key, bundle: null, loading: false,
          error: error instanceof Error ? error.message : String(error),
        });
      },
    );
    return () => { controller.abort(); };
  }, [sessionId, nodeId, key, attempt]);

  return {
    bundle: key && state.key === key ? state.bundle : null,
    loading: Boolean(key && (state.key !== key || state.loading)),
    error: key && state.key === key ? state.error : null,
    retry: () => setAttempt((current) => current + 1),
  };
}
