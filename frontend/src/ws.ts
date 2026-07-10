import { useEffect, useRef, useState } from "react";
import type { ClientMessage, ServerEvent } from "./types";

export type WSStatus = "connecting" | "open" | "closed";

export function useSessionSocket(
  sessionId: string | null,
  onEvent: (ev: ServerEvent) => void,
  activeNodeIds: string[] = [],
) {
  const [status, setStatus] = useState<WSStatus>("closed");
  const wsRef = useRef<WebSocket | null>(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const activeNodeIdsRef = useRef<Set<string>>(new Set());
  const lastSeqByNodeRef = useRef<Map<string, number>>(new Map());
  const requestedReplayNodeIdsRef = useRef<Set<string>>(new Set());
  const activeNodeIdsRefFromApp = useRef(activeNodeIds);
  activeNodeIdsRefFromApp.current = activeNodeIds;

  useEffect(() => {
    if (!sessionId) {
      setStatus("closed");
      activeNodeIdsRef.current.clear();
      lastSeqByNodeRef.current.clear();
      requestedReplayNodeIdsRef.current.clear();
      return;
    }
    // New session → drop any seq tracking from a previous session.
    activeNodeIdsRef.current.clear();
    lastSeqByNodeRef.current.clear();
    requestedReplayNodeIdsRef.current.clear();

    let cancelled = false;
    let ws: WebSocket | null = null;

    const connect = (isReconnect: boolean) => {
      if (cancelled) return;
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${window.location.host}/ws/${sessionId}`);
      wsRef.current = ws;
      setStatus("connecting");

      ws.onopen = () => {
        setStatus("open");
        if (ws?.readyState !== WebSocket.OPEN) return;
        requestedReplayNodeIdsRef.current.clear();
        const replayNodeIds = Array.from(new Set([
          ...activeNodeIdsRefFromApp.current,
          ...(isReconnect ? activeNodeIdsRef.current : []),
        ])).sort();
        if (replayNodeIds.length === 0) {
          ws.send(JSON.stringify({
            type: "replay_request",
            node_id: "",
            since_seq: 0,
          } satisfies ClientMessage));
        } else {
          for (const nodeId of replayNodeIds) {
            requestedReplayNodeIdsRef.current.add(nodeId);
            ws.send(JSON.stringify({
              type: "replay_request",
              node_id: nodeId,
              since_seq: lastSeqByNodeRef.current.get(nodeId) ?? 0,
            } satisfies ClientMessage));
          }
        }
      };
      ws.onclose = (e) => {
        setStatus("closed");
        // 4xxx codes are server-side rejections (e.g. 4404 session-not-found);
        // do not retry.
        if (!cancelled && (e.code < 4000 || e.code >= 5000)) {
          setTimeout(() => connect(true), 1000);
        }
      };
      ws.onerror = () => setStatus("closed");
      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data) as ServerEvent;
          /* Two seq regimes flow through this socket:
           *   - Runner-emitted events (per-node NodeRunner) carry monotonic
           *     seq >= 1 and ARE persisted to the event log. These need dedup
           *     across reconnect replays.
           *   - Registry-emitted events (virtual create/promote, node_removed,
           *     cross-node commit propagation, etc.) carry seq == 0 and are
           *     ephemeral (never persisted, never replayed). They MUST always
           *     be delivered — dropping them causes state divergence
           *     (e.g., node_removed lost → phantom nodes, or a stale refresh
           *     later wipes them anyway, producing the "canvas clears" bug).
           */
          const seq = typeof data.seq === "number" ? data.seq : null;
          const nodeId = "node_id" in data ? data.node_id : null;
          if (nodeId && seq !== null && seq > 0) {
            const lastSeq = lastSeqByNodeRef.current.get(nodeId) ?? 0;
            if (seq <= lastSeq) return;
            lastSeqByNodeRef.current.set(nodeId, seq);
          }
          if (data.type === "node_started") {
            activeNodeIdsRef.current.add(data.node_id);
          } else if (data.type === "turn_done") {
            activeNodeIdsRef.current.delete(data.node_id);
          } else if (
            data.type === "node_updated" &&
            !["queued", "running", "waiting", "awaiting_human_input"].includes(
              data.node.state,
            )
          ) {
            activeNodeIdsRef.current.delete(data.node.id);
          }
          /* seq == 0 (registry events) and missing-seq events fall through
           * to always-deliver. */
          onEventRef.current(data);
        } catch (err) {
          console.error("bad ws frame", err);
        }
      };
    };

    connect(false);

    return () => {
      cancelled = true;
      if (ws) ws.close();
      wsRef.current = null;
    };
  }, [sessionId]);

  useEffect(() => {
    const ws = wsRef.current;
    if (!sessionId || !ws || ws.readyState !== WebSocket.OPEN) return;
    for (const nodeId of [...activeNodeIds].sort()) {
      if (requestedReplayNodeIdsRef.current.has(nodeId)) continue;
      requestedReplayNodeIdsRef.current.add(nodeId);
      ws.send(JSON.stringify({
        type: "replay_request",
        node_id: nodeId,
        since_seq: lastSeqByNodeRef.current.get(nodeId) ?? 0,
      } satisfies ClientMessage));
    }
  }, [activeNodeIds, sessionId]);

  const send = (msg: ClientMessage) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  };

  // Development helper: close the live socket with a normal code so the
  // reconnect path fires with `(node_id, last_seq)`.
  const simulateDrop = () => {
    const ws = wsRef.current;
    if (ws) {
      ws.close(1000, "simulate-drop");
    }
  };

  return { status, send, simulateDrop };
}
