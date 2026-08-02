import { useEffect, useRef, useState } from "react";

import { wsSessionUrl } from "./bridge";

const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 10_000;

export interface ProjectSocket {
  /** Mirrors the live socket's `readyState === OPEN`, for consumers that
   * only need it for display (e.g. a status dot or placeholder text). */
  connected: boolean;
  reconnecting: boolean;
  /** Sends only if the socket is actually open right now (checked against
   * the raw `WebSocket`, not the `connected` state, so a caller that needs
   * to decide "did this really go out" — e.g. Companion's send-or-queue
   * choice — gets an answer with no render-lag race). Returns whether it
   * sent. */
  send: (event: object) => boolean;
  /** Registers a listener for every parsed downstream message, regardless
   * of shape — this hook doesn't know about turns or runs, only that one
   * socket exists. Returns an unsubscribe function. */
  subscribe: (handler: (message: unknown) => void) => () => void;
}

/**
 * The project's one WebSocket session (backend/ws/__init__.py: `_sessions`
 * holds exactly one live connection per `project_id`, unconditionally
 * overwritten by whoever connects last) — owned once here, at the
 * `ProjectShell` level, so `CompanionPane` and `ExperimentsBoard` can both
 * react to it without either opening a second connection that would
 * silently evict the other's session from the backend's registry.
 *
 * Deliberately thin: reconnect-with-backoff is the only behavior this hook
 * owns. It does not parse `event` discriminators, queue anything, or know
 * what a turn or a run is — each consumer subscribes and filters for its
 * own event shapes, and (for Companion) does its own queue-while-
 * disconnected bookkeeping against `send`'s return value.
 */
export function useProjectSocket(projectId: string): ProjectSocket {
  const [connected, setConnected] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const listenersRef = useRef<Set<(message: unknown) => void>>(new Set());
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closedByUsRef = useRef(false);

  useEffect(() => {
    closedByUsRef.current = false;
    reconnectAttemptRef.current = 0;

    function connect(): void {
      const ws = new WebSocket(wsSessionUrl(projectId));
      wsRef.current = ws;
      ws.onopen = () => {
        setConnected(true);
        setReconnecting(false);
        reconnectAttemptRef.current = 0;
      };
      ws.onclose = () => {
        setConnected(false);
        wsRef.current = null;
        if (closedByUsRef.current) return;
        setReconnecting(true);
        const delay = Math.min(RECONNECT_BASE_MS * 2 ** reconnectAttemptRef.current, RECONNECT_MAX_MS);
        reconnectAttemptRef.current += 1;
        reconnectTimerRef.current = setTimeout(connect, delay);
      };
      ws.onmessage = (event) => {
        const parsed: unknown = JSON.parse(event.data);
        for (const listener of listenersRef.current) listener(parsed);
      };
    }

    connect();
    return () => {
      closedByUsRef.current = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [projectId]);

  function send(event: object): boolean {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(event));
    return true;
  }

  function subscribe(handler: (message: unknown) => void): () => void {
    listenersRef.current.add(handler);
    return () => {
      listenersRef.current.delete(handler);
    };
  }

  return { connected, reconnecting, send, subscribe };
}
