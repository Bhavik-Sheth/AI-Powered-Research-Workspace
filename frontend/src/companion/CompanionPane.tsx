import { useEffect, useRef, useState } from "react";

import { wsSessionUrl } from "../state/bridge";
import "./CompanionPane.css";
import { renderAssistantContent } from "./parseCitations";
import type { DownstreamEvent, SelectionState } from "./wsTypes";

interface TranscriptEntry {
  id: number;
  role: "user" | "assistant" | "error";
  content: string;
}

export interface PendingAsk {
  text: string;
  nonce: number;
}

/** The Companion pane (MODULES.md Session Transport + Agent Harness,
 * UI_DESIGN.md §3.1) — one WebSocket session per project, streaming a
 * turn's `status` / `text_delta` / `turn_complete` / `error` events into a
 * transcript that keeps cited evidence visually distinct from reasoning
 * (D24), the split the UI must never collapse.
 */
export function CompanionPane({
  projectId,
  selection,
  pendingAsk,
}: {
  projectId: string;
  selection: SelectionState | null;
  pendingAsk: PendingAsk | null;
}) {
  const [connected, setConnected] = useState(false);
  const [statusText, setStatusText] = useState<string | null>(null);
  const [turnInFlight, setTurnInFlight] = useState(false);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [draft, setDraft] = useState("");
  const wsRef = useRef<WebSocket | null>(null);
  const assistantBufferRef = useRef("");
  const nextIdRef = useRef(0);
  const handledNonceRef = useRef<number | null>(null);
  const selectionRef = useRef(selection);
  selectionRef.current = selection;

  useEffect(() => {
    const ws = new WebSocket(wsSessionUrl(projectId));
    wsRef.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (event) => {
      const parsed: DownstreamEvent = JSON.parse(event.data);
      handleDownstream(parsed);
    };
    return () => {
      ws.close();
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  function nextId(): number {
    nextIdRef.current += 1;
    return nextIdRef.current;
  }

  function handleDownstream(evt: DownstreamEvent): void {
    if (evt.event === "status") {
      setStatusText(evt.text);
    } else if (evt.event === "text_delta") {
      assistantBufferRef.current += evt.delta;
    } else if (evt.event === "turn_complete") {
      const content = assistantBufferRef.current;
      assistantBufferRef.current = "";
      setTurnInFlight(false);
      setStatusText(null);
      if (content) {
        setTranscript((prev) => [...prev, { id: nextId(), role: "assistant", content }]);
      }
    } else if (evt.event === "error") {
      setTurnInFlight(false);
      setStatusText(null);
      setTranscript((prev) => [...prev, { id: nextId(), role: "error", content: evt.message }]);
    }
  }

  function sendMessage(text: string, withSelection: SelectionState | null): void {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || turnInFlight || !text.trim()) return;
    setTranscript((prev) => [...prev, { id: nextId(), role: "user", content: text }]);
    setTurnInFlight(true);
    ws.send(JSON.stringify({ event: "user_message", text, ui_state: { selection: withSelection } }));
  }

  useEffect(() => {
    if (pendingAsk && pendingAsk.nonce !== handledNonceRef.current) {
      handledNonceRef.current = pendingAsk.nonce;
      sendMessage(pendingAsk.text, selectionRef.current);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingAsk?.nonce]);

  function handleSubmit(): void {
    sendMessage(draft, selection);
    setDraft("");
  }

  return (
    <aside className="companion">
      <div className="companion__status">
        <span className={`companion__status-dot ${connected ? "companion__status-dot--live" : ""}`} />
        <span className="companion__status-text">{statusText ?? (connected ? "Companion" : "Connecting…")}</span>
        {turnInFlight && (
          <button type="button" className="companion__stop" onClick={() => wsRef.current?.send(JSON.stringify({ event: "interrupt", turn_id: "" }))}>
            ✕ Stop
          </button>
        )}
      </div>

      <div className="companion__transcript">
        {transcript.length === 0 && (
          <div className="companion__empty">
            <div className="companion__empty-circle" />
            <p className="companion__empty-title">No conversation yet in this project.</p>
            <p className="companion__empty-body">Ask about a paper, a claim, or your notes.</p>
          </div>
        )}
        {transcript.map((entry) => {
          if (entry.role === "user") {
            return (
              <div key={entry.id} className="companion__bubble companion__bubble--user">
                {entry.content}
              </div>
            );
          }
          if (entry.role === "error") {
            return (
              <div key={entry.id} className="companion__error">
                {entry.content}
              </div>
            );
          }
          return (
            <div key={entry.id} className="companion__bubble companion__bubble--assistant">
              {renderAssistantContent(entry.content)}
            </div>
          );
        })}
      </div>

      <div className="companion__composer">
        <input
          className="companion__input"
          placeholder="Ask the companion…"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") handleSubmit();
          }}
        />
        <button type="button" className="companion__mic" aria-label="Voice input (coming in Voice.1)" disabled />
        <button type="button" className="companion__send" aria-label="Send" onClick={handleSubmit}>
          ↑
        </button>
      </div>
    </aside>
  );
}
