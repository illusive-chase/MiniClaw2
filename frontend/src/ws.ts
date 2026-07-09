import { useEffect, useRef, useState } from "react";
import type { ClientMessage, ServerEvent } from "./types";

export type WSStatus = "connecting" | "open" | "closed";

export function useSessionSocket(
  sessionId: string | null,
  onEvent: (ev: ServerEvent) => void,
) {
  const [status, setStatus] = useState<WSStatus>("closed");
  const wsRef = useRef<WebSocket | null>(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const activeNodeIdRef = useRef<string | null>(null);
  const lastSeqRef = useRef<number>(0);

  useEffect(() => {
    if (!sessionId) {
      setStatus("closed");
      activeNodeIdRef.current = null;
      lastSeqRef.current = 0;
      return;
    }
    // New session → drop any seq tracking from a previous session.
    activeNodeIdRef.current = null;
    lastSeqRef.current = 0;

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
        const nodeId = isReconnect ? activeNodeIdRef.current : null;
        const sinceSeq = isReconnect ? lastSeqRef.current : 0;
        const msg: ClientMessage = {
          type: "replay_request",
          node_id: nodeId ?? "",
          since_seq: sinceSeq,
        };
        ws.send(JSON.stringify(msg));
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
          if (data.type === "node_started") {
            if (
              activeNodeIdRef.current === data.node_id &&
              seq !== null &&
              seq > 0 &&
              seq <= lastSeqRef.current
            ) {
              return;
            }
            activeNodeIdRef.current = data.node_id;
            if (seq !== null && seq > 0) lastSeqRef.current = seq;
          } else if (seq !== null && seq > 0) {
            if (seq <= lastSeqRef.current) return;
            lastSeqRef.current = seq;
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
