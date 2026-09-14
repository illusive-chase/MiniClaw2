import { useEffect, useState } from "react";
import { getNodeDetail } from "./api";
import { NodeDetailCache, nodeDetailKey } from "./nodeDetailCache";
import type { NodeDetail, NodeInfo } from "./types";

const cache = new NodeDetailCache(getNodeDetail);

export function useNodeDetail(sessionId: string | undefined, node: NodeInfo | undefined) {
  const nodeId = node?.id;
  const rev = node?.rev ?? 0;
  const key = sessionId && nodeId ? nodeDetailKey(sessionId, nodeId, rev) : null;
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{
    key: string | null;
    detail: NodeDetail | null;
    loading: boolean;
    error: string | null;
  }>({ key: null, detail: null, loading: false, error: null });

  useEffect(() => {
    if (!sessionId || !nodeId) return;
    let cancelled = false;
    const cached = cache.get(sessionId, nodeId, rev);
    setState((current) => ({
      key,
      detail: cached ?? (
        current.detail?.id === nodeId && current.detail.project_id === sessionId
          ? current.detail : null
      ),
      loading: !cached,
      error: null,
    }));
    if (!cached) {
      void cache.load(sessionId, nodeId, rev).then(
        (detail) => {
          if (!cancelled) setState({ key, detail, loading: false, error: null });
        },
        (error: unknown) => {
          if (!cancelled) setState((current) => ({
            ...current,
            loading: false,
            error: error instanceof Error ? error.message : String(error),
          }));
        },
      );
    }
    return () => { cancelled = true; };
  }, [sessionId, nodeId, rev, key, attempt]);

  return {
    detail: state.detail && state.detail.id === nodeId && state.detail.project_id === sessionId
      ? state.detail : null,
    loading: key !== null && (state.key !== key || state.loading),
    error: state.key === key ? state.error : null,
    retry: () => setAttempt((current) => current + 1),
  };
}
