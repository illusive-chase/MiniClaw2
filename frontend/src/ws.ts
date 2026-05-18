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

  useEffect(() => {
    if (!sessionId) {
      setStatus("closed");
      return;
    }
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/${sessionId}`);
    wsRef.current = ws;
    setStatus("connecting");

    ws.onopen = () => setStatus("open");
    ws.onclose = () => setStatus("closed");
    ws.onerror = () => setStatus("closed");
    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data) as ServerEvent;
        onEventRef.current(data);
      } catch (err) {
        console.error("bad ws frame", err);
      }
    };

    return () => {
      ws.close();
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
