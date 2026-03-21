import { useEffect, useRef, useCallback } from "react";

/**
 * Connect to the factory WebSocket and call onUpdate when artifacts change.
 * Falls back to polling if WebSocket fails.
 * Reconnects automatically on disconnect.
 */
export function useFactorySocket(onUpdate: () => void, pollMs = 5000) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const connectedRef = useRef(false);

  const connect = useCallback(() => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/api/ws`);

    ws.onopen = () => {
      connectedRef.current = true;
      // Stop polling when WebSocket is live
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "artifacts_changed") {
          onUpdate();
        }
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      connectedRef.current = false;
      wsRef.current = null;
      // Fall back to polling
      if (!pollRef.current) {
        pollRef.current = setInterval(onUpdate, pollMs);
      }
      // Reconnect after delay
      reconnectRef.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, [onUpdate, pollMs]);

  useEffect(() => {
    // Initial load
    onUpdate();
    // Start with polling, upgrade to WebSocket
    pollRef.current = setInterval(onUpdate, pollMs);
    connect();

    return () => {
      if (wsRef.current) wsRef.current.close();
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [connect, onUpdate, pollMs]);
}
