import { useEffect, useRef, useState } from "react";
import { getConversationApiProjectsProjectIdConversationGet } from "@research-os/api-client";

import type { ProjectSocket } from "../state/useProjectSocket";
import { useVoice } from "../voice/useVoice";
import "./CompanionPane.css";
import { renderAssistantContent } from "./parseCitations";
import type { Citation, DownstreamEvent, SelectionState } from "./wsTypes";

/**
 * Validates every field a given `event` discriminator requires, not just
 * the discriminator itself — a backend shape drift on e.g. `tool_result`
 * would otherwise pass the old event-only check and throw "Objects are not
 * valid as a React child" once `model_view` reached the transcript
 * (Frontend Improvement Plan Phase 1.3). Mirrors `DownstreamEvent` in
 * wsTypes.ts field for field.
 */
function isDownstreamEvent(message: unknown): message is DownstreamEvent {
  if (typeof message !== "object" || message === null || !("event" in message)) return false;
  const m = message as Record<string, unknown>;
  switch (m.event) {
    case "status":
      return typeof m.text === "string";
    case "text_delta":
      return typeof m.delta === "string";
    case "tool_call":
      return typeof m.tool_name === "string" && typeof m.args === "object" && m.args !== null;
    case "tool_result":
      return (
        typeof m.tool_name === "string" &&
        typeof m.model_view === "string" &&
        (m.result_id === null || typeof m.result_id === "string")
      );
    case "ui_action":
      return typeof m.action === "string" && typeof m.payload === "object" && m.payload !== null;
    case "turn_complete":
      return (
        typeof m.turn_id === "string" &&
        typeof m.interrupted === "boolean" &&
        typeof m.iterations === "number" &&
        Array.isArray(m.citations)
      );
    case "error":
      return (
        typeof m.code === "string" &&
        typeof m.message === "string" &&
        typeof m.recoverable === "boolean" &&
        (m.what_still_worked === null || typeof m.what_still_worked === "string")
      );
    default:
      return false;
  }
}

interface TranscriptEntry {
  id: number;
  role: "user" | "assistant" | "error" | "tool";
  content: string;
  /** Only ever set on an `assistant` entry — the `<cite>` spans `content`
   * contains, in the same order (Phase 6.1). */
  citations?: Citation[];
}

interface QueuedMessage {
  text: string;
  selection: SelectionState | null;
  openPaperIds: string[];
  inputModality: "text" | "voice";
}

export interface PendingAsk {
  text: string;
  nonce: number;
}

/** The Companion pane (MODULES.md Session Transport + Agent Harness,
 * UI_DESIGN.md §3.1) — one WebSocket session per project, streaming a
 * turn's `status` / `text_delta` / `turn_complete` / `error` events into a
 * transcript that keeps cited evidence visually distinct from reasoning
 * (D24), the split the UI must never collapse. Reconnects with backoff on
 * a dropped socket (TRD §4.1); a send while disconnected queues and the
 * composer says so, rather than silently failing. A send while a turn is
 * already running is not blocked client-side either — it goes out and the
 * backend's `turn_in_progress` error comes back into the transcript, next
 * to the still-live `✕ Stop` for the turn that's actually in the way.
 *
 * The socket itself is owned by `ProjectShell` (`useProjectSocket`), not by
 * this component — Phase 2.3 added a second consumer of the same
 * per-project connection (`ExperimentsBoard`'s run-log streaming), and the
 * backend allows exactly one live connection per project, so only one
 * component may ever call `new WebSocket(...)`. This component only
 * subscribes and sends through the shared handle.
 */
export function CompanionPane({
  projectId,
  selection,
  pendingAsk,
  socket,
  openPaperIds,
  onUIAction,
  onCiteClick,
}: {
  projectId: string;
  selection: SelectionState | null;
  pendingAsk: PendingAsk | null;
  socket: ProjectSocket;
  /** Paper ids currently open as reader tabs — the read set a cross-paper
   * compare claim (US4) is allowed to cite (MODULES.md Agent Harness). */
  openPaperIds: string[];
  /** A tool result's `ui_action` — the same route transition the user's
   * own click would produce (Bug Fix Plan Phase 2.3). */
  onUIAction: (action: string, payload: Record<string, unknown>) => void;
  /** An anchor-backed citation was clicked — drives `scroll_to` +
   * `highlight_span` in whichever reader tab it belongs to (Phase 6.1). */
  onCiteClick: (citation: Citation) => void;
}) {
  const [statusText, setStatusText] = useState<string | null>(null);
  const [turnInFlight, setTurnInFlight] = useState(false);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [queued, setQueued] = useState<QueuedMessage | null>(null);
  const assistantBufferRef = useRef("");
  const nextIdRef = useRef(0);
  const handledNonceRef = useRef<number | null>(null);
  const selectionRef = useRef(selection);
  const queuedRef = useRef<QueuedMessage | null>(null);
  // History must finish loading (TRD §4.1: seeding after a live message
  // arrives would clobber it) before any downstream message is applied.
  // The shared socket connects independently of this component's mount now,
  // so — unlike the old single-effect `loadHistoryThenConnect` — a message
  // can genuinely arrive before history is ready; buffer and replay instead
  // of dropping or racing it.
  const historyLoadedRef = useRef(false);
  const bufferedEventsRef = useRef<DownstreamEvent[]>([]);
  selectionRef.current = selection;
  queuedRef.current = queued;

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
        setTranscript((prev) => [...prev, { id: nextId(), role: "assistant", content, citations: evt.citations }]);
      }
    } else if (evt.event === "tool_call") {
      setStatusText(`${evt.tool_name}…`);
    } else if (evt.event === "tool_result") {
      setStatusText(null);
      setTranscript((prev) => [...prev, { id: nextId(), role: "tool", content: evt.model_view }]);
    } else if (evt.event === "ui_action") {
      onUIAction(evt.action, evt.payload);
    } else if (evt.event === "error") {
      // `turn_in_progress` means this specific send was refused — the turn
      // it collided with is still running (backend/ws/__init__.py never
      // schedules a task for the rejected message), so the status line and
      // ✕ Stop must keep reflecting that turn rather than going idle under
      // a message that never actually stopped anything.
      if (evt.code !== "turn_in_progress") {
        setTurnInFlight(false);
        setStatusText(null);
      }
      setTranscript((prev) => [...prev, { id: nextId(), role: "error", content: evt.message }]);
    }
  }

  // Rehydrate the verbatim transcript (TRD §4.1). Previously this gated
  // *opening* the socket; now the socket is shared and connects on its own
  // schedule, so instead this gates *applying* downstream messages — see
  // the subscribe effect below, which buffers anything that arrives first.
  useEffect(() => {
    historyLoadedRef.current = false;
    bufferedEventsRef.current = [];
    let cancelled = false;

    async function loadHistory(): Promise<void> {
      try {
        const { data } = await getConversationApiProjectsProjectIdConversationGet({
          path: { project_id: projectId },
          throwOnError: true,
        });
        if (cancelled) return;
        const seeded = data.messages
          .filter((message) => message.role === "user" || message.role === "assistant")
          .map((message) => ({
            id: nextId(),
            role: message.role as "user" | "assistant",
            content: message.content,
            citations: message.role === "assistant" ? message.citations : undefined,
          }));
        setTranscript(seeded);
      } catch {
        // A history-load failure shouldn't block the live connection —
        // worst case the transcript starts empty, same as before this.
      }
      if (cancelled) return;
      historyLoadedRef.current = true;
      const buffered = bufferedEventsRef.current;
      bufferedEventsRef.current = [];
      for (const evt of buffered) handleDownstream(evt);
    }

    void loadHistory();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    return socket.subscribe((message) => {
      if (!isDownstreamEvent(message)) return; // not ours — e.g. a run_log/run_status for ExperimentsBoard
      if (!historyLoadedRef.current) {
        bufferedEventsRef.current.push(message);
        return;
      }
      handleDownstream(message);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [socket]);

  // Flush a message queued while disconnected as soon as the shared socket
  // reports connected — equivalent to the old `ws.onopen` flush, just
  // reacting to the hook's `connected` transition instead of owning `onopen`
  // directly.
  useEffect(() => {
    if (!socket.connected) return;
    const pending = queuedRef.current;
    if (!pending) return;
    setQueued(null);
    socket.send({
      event: "user_message",
      text: pending.text,
      ui_state: { selection: pending.selection, open_paper_ids: pending.openPaperIds },
      input_modality: pending.inputModality,
    });
    setTurnInFlight(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [socket.connected]);

  // A mid-turn socket drop otherwise leaves `turnInFlight` stuck forever —
  // the closed socket can never deliver that turn's `turn_complete`/`error`,
  // so `✕ Stop` would stay live and the transcript would look permanently
  // "in progress" even once `useProjectSocket` reconnects (Phase 6.1).
  // Reconnect-with-backoff itself is already handled there; this only
  // clears the now-orphaned turn state and says so, flushing whatever
  // partial answer had already streamed in rather than discarding it
  // (Rules.md: "partial results are never rolled back").
  const wasConnectedRef = useRef(socket.connected);
  useEffect(() => {
    const wasConnected = wasConnectedRef.current;
    wasConnectedRef.current = socket.connected;
    if (!wasConnected || socket.connected || !turnInFlight) return;
    const partial = assistantBufferRef.current;
    assistantBufferRef.current = "";
    setTurnInFlight(false);
    setStatusText(null);
    setTranscript((prev) => {
      const withPartial = partial ? [...prev, { id: nextId(), role: "assistant" as const, content: partial }] : prev;
      return [
        ...withPartial,
        { id: nextId(), role: "error" as const, content: "Connection lost mid-turn — the response may be incomplete. Try asking again." },
      ];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [socket.connected, turnInFlight]);

  function sendMessage(text: string, withSelection: SelectionState | null, inputModality: "text" | "voice" = "text"): void {
    // Empty input has nothing to say anything about, so it stays a silent
    // no-op. A turn already being in flight is different: the backend
    // already has an answer for that case (the `turn_in_progress` error
    // below), so the message goes out regardless and lets that answer
    // reach the transcript instead of being swallowed here first.
    if (!text.trim()) return;
    const sent = socket.send({
      event: "user_message",
      text,
      ui_state: { selection: withSelection, open_paper_ids: openPaperIds },
      input_modality: inputModality,
    });
    if (!sent) {
      setQueued({ text, selection: withSelection, openPaperIds, inputModality });
      setTranscript((prev) => [...prev, { id: nextId(), role: "user", content: text }]);
      return;
    }
    setTranscript((prev) => [...prev, { id: nextId(), role: "user", content: text }]);
    setTurnInFlight(true);
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

  const { recording, startRecording, transcribeOnRelease } = useVoice();

  async function handleMicRelease(): Promise<void> {
    if (!recording) return;
    const text = await transcribeOnRelease();
    if (text) sendMessage(text, selection, "voice");
  }

  const statusLine = queued
    ? "Disconnected — your message will send once reconnected…"
    : (statusText ?? (socket.connected ? "Companion" : socket.reconnecting ? "Reconnecting…" : "Connecting…"));

  return (
    <aside className="companion">
      <div className="companion__status">
        <span className={`companion__status-dot ${socket.connected ? "companion__status-dot--live" : ""}`} />
        <span className="companion__status-text">{statusLine}</span>
        {turnInFlight && (
          <button type="button" className="companion__stop" onClick={() => socket.send({ event: "interrupt" })}>
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
          if (entry.role === "tool") {
            return (
              <div key={entry.id} className="companion__tool-result">
                {entry.content}
              </div>
            );
          }
          return (
            <div key={entry.id} className="companion__bubble companion__bubble--assistant">
              {renderAssistantContent(entry.content, entry.citations, onCiteClick)}
            </div>
          );
        })}
      </div>

      <div className="companion__composer">
        <input
          className="companion__input"
          placeholder={socket.connected ? "Ask the companion…" : "Message will queue until reconnected…"}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") handleSubmit();
          }}
        />
        <button
          type="button"
          className={`companion__mic ${recording ? "companion__mic--recording" : ""}`}
          aria-label="Hold to talk"
          onMouseDown={() => void startRecording()}
          onMouseUp={() => void handleMicRelease()}
          onMouseLeave={() => recording && void handleMicRelease()}
          onKeyDown={(event) => {
            // A held key auto-repeats `keydown` — without the `repeat`
            // guard (and the `recording` check) every repeat would call
            // `startRecording()` again on top of an already-open recording.
            if ((event.key === "Enter" || event.key === " ") && !event.repeat && !recording) {
              event.preventDefault();
              void startRecording();
            }
          }}
          onKeyUp={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              void handleMicRelease();
            }
          }}
          onTouchStart={(event) => {
            // Without this, the browser's compatibility mousedown/mouseup
            // pair that normally follows a touch would call startRecording
            // a second time on top of this one.
            event.preventDefault();
            if (!recording) void startRecording();
          }}
          onTouchEnd={(event) => {
            event.preventDefault();
            void handleMicRelease();
          }}
        />
        <button type="button" className="companion__send" aria-label="Send" onClick={handleSubmit}>
          ↑
        </button>
      </div>
    </aside>
  );
}
