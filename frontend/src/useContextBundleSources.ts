import { useEffect, useRef, useState } from "react";
import { getContextBundleSources } from "./api";
import { ContextBundleSourcesLoader, type ContextBundleSourcesByNodeId } from "./contextBundleSources";
import type { NodeInfo } from "./types";

const EMPTY: ContextBundleSourcesByNodeId = {};

export function useContextBundleSources(sessionId: string | null, nodes: NodeInfo[]) {
  const loaderRef = useRef<ContextBundleSourcesLoader | null>(null);
  const [state, setState] = useState<{
    sessionId: string | null;
    bundles: ContextBundleSourcesByNodeId;
  }>({ sessionId: null, bundles: EMPTY });

  useEffect(() => {
    setState({ sessionId, bundles: EMPTY });
    const loader = sessionId ? new ContextBundleSourcesLoader(
      sessionId,
      getContextBundleSources,
      (bundles) => setState({ sessionId, bundles }),
      (error) => console.warn("批量读取上下文来源失败", error),
    ) : null;
    loaderRef.current = loader;
    return () => { loader?.dispose(); };
  }, [sessionId]);

  useEffect(() => {
    loaderRef.current?.update(nodes);
  }, [sessionId, nodes]);

  return state.sessionId === sessionId ? state.bundles : EMPTY;
}
