import { useEffect, useState } from "react";

import { artifactRawUrl } from "../api";
import {
  ConcurrentWarning,
  DiffHeader,
  DiffViewer,
  parseDiffArtifact,
  type DiffArtifact,
} from "../components/DiffViewer";
import type { MarkdownRoute } from "../markdownRoute";
import { applyStoredTheme } from "../theme";

export function DiffViewerPage({ route }: { route: Extract<MarkdownRoute, { src: "diff" }> }) {
  const [artifact, setArtifact] = useState<DiffArtifact | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => applyStoredTheme(), []);
  useEffect(() => {
    let cancelled = false;
    fetch(artifactRawUrl(route.sessionId, route.nodeId, route.name))
      .then(async (response) => {
        if (!response.ok) throw new Error(`加载失败：${response.status}`);
        const parsed = parseDiffArtifact(await response.json());
        if (!parsed) throw new Error("产物不是受支持的 diff 格式");
        return parsed;
      })
      .then((value) => {
        if (!cancelled) {
          setArtifact(value);
          document.title = `${route.name} · MiniClaw2`;
        }
      })
      .catch((reason) => !cancelled && setError(String(reason)));
    return () => { cancelled = true; };
  }, [route]);

  if (error) return <div className="min-h-screen bg-surface p-6 text-sm text-state-error">{error}</div>;
  if (!artifact) return <div className="min-h-screen bg-surface p-6 text-sm text-ink-muted">加载中...</div>;
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-surface text-ink">
      <DiffHeader artifact={artifact} sessionId={route.sessionId} nodeId={route.nodeId} name={route.name} />
      <ConcurrentWarning ids={artifact.concurrent_node_ids} />
      <DiffViewer artifact={artifact} />
    </div>
  );
}
