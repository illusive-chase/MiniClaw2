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
        if (
          isReconnect &&
          activeNodeIdRef.current &&
          lastSeqRef.current > 0 &&
          ws?.readyState === WebSocket.OPEN
        ) {
          const msg: ClientMessage = {
            type: "replay_request",
            node_id: activeNodeIdRef.current,
            since_seq: lastSeqRef.current,
          };
          ws.send(JSON.stringify(msg));
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
          if (data.type === "node_started") {
            activeNodeIdRef.current = data.node_id;
            lastSeqRef.current = data.seq ?? 0;
          } else if (typeof data.seq === "number" && data.seq > lastSeqRef.current) {
            lastSeqRef.current = data.seq;
          }
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

  return { status, send };
}
