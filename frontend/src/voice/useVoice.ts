import { useEffect, useRef, useState } from "react";

import { usePttBinding } from "../state/usePttBinding";
import { postBinaryForJson, postJsonForBinary } from "../state/bridge";
import { isModifierKey, keysForBinding, type ModifierKey } from "./pttBinding";

// Voice Layer Plan V13: long enough to read and cancel a short misheard
// utterance, short enough that a turn the user actually meant to send
// isn't kept waiting.
const CANCEL_WINDOW_MS = 2000;

// Live captions while the mic is held — `faster_whisper.py` transcribes a
// whole clip at once, it has no incremental/streaming decode mode, so this
// is a best-effort approximation: every tick, whatever's been recorded
// *so far* is re-transcribed from the start and replaces the caption.
// 1.2s balances "feels live" against re-running the STT engine on a
// steadily growing clip — a much shorter interval would mean the previous
// poll is still often in flight when the next one would fire (the in-flight
// guard below just skips that tick rather than piling up requests).
const LIVE_CAPTION_POLL_MS = 1200;

// Splits accumulated assistant text into complete sentences as it streams
// (V7) — a terminator only counts once it's followed by real whitespace
// already in the buffer, never by "end of buffer so far", so a period that
// turns out to be mid-abbreviation (more text streams in right after)
// never gets treated as a boundary before it actually is one.
const SENTENCE_BOUNDARY = /[.!?]+(?:["')\]]*)\s+/g;

// The harness's own citation markup (D24) — read the cited evidence inline
// as prose (V6), not "bracket n" (there is no `[n]` in the raw stream at
// all; that superscript is `parseCitations.tsx`'s own render-time
// artifact, so V6's "no citation numbers read aloud" is already satisfied
// by never introducing one here).
const CITE_TAG = /<(cite|unverified)>([\s\S]*?)<\/\1>/g;
const CODE_FENCE = /```[\s\S]*?```/g;

/** Net open/close depth of `<cite>`/`<unverified>` tags up to this point in
 * the buffer — >0 means a sentence boundary here would fall mid-quote. */
function citeTagDepth(text: string): number {
  const opens = (text.match(/<(cite|unverified)>/g) ?? []).length;
  const closes = (text.match(/<\/(cite|unverified)>/g) ?? []).length;
  return opens - closes;
}

/** True if the buffer up to this point has an unterminated ``` fence. */
function insideCodeFence(text: string): boolean {
  return (text.match(/```/g) ?? []).length % 2 === 1;
}

/** Pulls every sentence that can be safely spoken out of `text`, holding
 * back anything still inside an open citation tag or code fence (Voice
 * Layer Plan §6 risk: "the splitter must hold text back until a terminator
 * appears outside a fence") — those keep growing in `remainder` until a
 * later call sees them closed. */
function splitCompleteSentences(text: string): { sentences: string[]; remainder: string } {
  const sentences: string[] = [];
  let cursor = 0;
  SENTENCE_BOUNDARY.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = SENTENCE_BOUNDARY.exec(text)) !== null) {
    const end = match.index + match[0].length;
    const soFar = text.slice(0, end);
    if (citeTagDepth(soFar) === 0 && !insideCodeFence(soFar)) {
      sentences.push(text.slice(cursor, end));
      cursor = end;
    }
  }
  return { sentences, remainder: text.slice(cursor) };
}

/** Raw streamed assistant text -> what should actually be spoken (V6): cited
 * evidence read inline as prose, markdown syntax stripped, a code fence
 * skipped entirely (reading a code block aloud is not useful prose). No
 * second LLM call — this is a fixed, non-LLM transform, same spirit as the
 * provenance validator being deterministic. */
function cleanForSpeech(raw: string): string {
  return raw
    .replace(CITE_TAG, "$2")
    .replace(CODE_FENCE, "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/\*(.+?)\*/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^[-*+]\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** Voice Capture (MODULES.md, D37) — the only module touching
 * `getUserMedia` or an audio element. Push-to-talk two ways: the mouse/touch
 * mic button (unchanged — hold to record, release to transcribe and send
 * immediately), and holding the configured chord anywhere in the app
 * (Voice.6, MODULES.md's key machine) — release transcribes the same way
 * but defers the send behind a `CANCEL_WINDOW_MS` undo window, since a held
 * key has no visual "are you sure" the mouse button's own affordance gives
 * for free. Either path sends through the identical Companion path as a
 * typed turn, tagged `input_modality: "voice"`.
 *
 * `onSendVoiceMessage` is called once the chord-triggered cancel window
 * elapses uncancelled — this hook owns capture/transcribe/timing, the
 * caller (`CompanionPane`) owns what "send" actually means (the WebSocket).
 *
 * Voice.7: a reply to a voice-triggered turn is spoken as it streams.
 * `beginVoiceTurn`/`feedAssistantDelta`/`endVoiceTurn` are `CompanionPane`'s
 * hooks into that — call `beginVoiceTurn` when a voice-tagged send goes
 * out, `feedAssistantDelta` on every `text_delta` while that turn is still
 * the current one, `endVoiceTurn` on its `turn_complete`. Sentences are
 * synthesized and played back-to-back through an internal queue as they
 * complete, not held until the whole turn finishes. `stopPlayback` (wired
 * to `✕ Stop`, and called automatically on a barge-in) clears the queue
 * and cuts whatever is currently playing; it also mutes the rest of the
 * turn that was playing, so a still-streaming interrupted turn's later
 * sentences don't quietly start speaking again a moment later.
 */
export function useVoice(onSendVoiceMessage: (text: string) => void) {
  const [recording, setRecording] = useState(false);
  // A denied microphone permission (or any other capture/transcribe/synthesize
  // failure) lands here instead of an unhandled rejection, so the caller can
  // disable push-to-talk with an explanatory tooltip rather than a silent
  // no-op (MODULES.md Voice Capture: Errors).
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  // The chord-triggered transcript, while its cancel window is still open —
  // null once it's either sent or cancelled.
  const [pendingVoiceMessage, setPendingVoiceMessage] = useState<string | null>(null);
  const pendingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onSendRef = useRef(onSendVoiceMessage);
  onSendRef.current = onSendVoiceMessage;

  // Best-effort live caption of the clip recorded so far — null whenever
  // not actively holding the mic. See `LIVE_CAPTION_POLL_MS`'s own comment
  // for why this re-transcribes from the start each tick rather than
  // streaming incrementally.
  const [liveCaption, setLiveCaption] = useState<string | null>(null);
  const liveCaptionTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const liveCaptionInFlightRef = useRef(false);

  function stopLiveCaptionPolling(): void {
    if (liveCaptionTimerRef.current !== null) {
      clearInterval(liveCaptionTimerRef.current);
      liveCaptionTimerRef.current = null;
    }
    setLiveCaption(null);
  }

  async function pollLiveCaption(mimeType: string): Promise<void> {
    if (liveCaptionInFlightRef.current || chunksRef.current.length === 0) return;
    liveCaptionInFlightRef.current = true;
    try {
      const blobSoFar = new Blob(chunksRef.current, { type: mimeType });
      const { text } = await postBinaryForJson<{ text: string }>("/api/voice/transcribe", await blobSoFar.arrayBuffer());
      // Recording may have stopped while this request was in flight —
      // don't resurrect a caption for a hold that already ended.
      if (recorderRef.current) setLiveCaption(text);
    } catch {
      // A single failed poll just leaves the last caption showing; the
      // final transcribe on release (`transcribeOnRelease`) is what
      // actually matters and reports its own error independently.
    } finally {
      liveCaptionInFlightRef.current = false;
    }
  }

  async function startRecording(): Promise<void> {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      // A timeslice, not a bare `start()` — makes `ondataavailable` fire
      // periodically *while still recording* instead of only once on
      // `stop()`, which is what lets the live-caption poll below see
      // anything before the hold ends.
      recorder.start(LIVE_CAPTION_POLL_MS);
      recorderRef.current = recorder;
      setRecording(true);
      setLiveCaption(null);
      liveCaptionTimerRef.current = setInterval(() => void pollLiveCaption(recorder.mimeType), LIVE_CAPTION_POLL_MS);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not access the microphone");
    }
  }

  function stopRecording(): Promise<Blob | null> {
    return new Promise((resolve) => {
      stopLiveCaptionPolling();
      const recorder = recorderRef.current;
      if (!recorder) {
        resolve(null);
        return;
      }
      recorder.onstop = () => {
        recorder.stream.getTracks().forEach((track) => track.stop());
        recorderRef.current = null;
        setRecording(false);
        resolve(new Blob(chunksRef.current, { type: recorder.mimeType }));
      };
      recorder.stop();
    });
  }

  /** Ends the push-to-talk hold and returns the transcribed text, or `null`
   * if nothing was recorded. */
  async function transcribeOnRelease(): Promise<string | null> {
    const blob = await stopRecording();
    if (!blob || blob.size === 0) return null;
    setError(null);
    try {
      const { text } = await postBinaryForJson<{ text: string }>("/api/voice/transcribe", await blob.arrayBuffer());
      return text;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not transcribe the recording");
      return null;
    }
  }

  // The sentence playback queue (V7) — a growing raw-text buffer for the
  // turn currently being spoken, the cleaned sentences waiting on
  // synthesis+playback, and the currently-playing `Audio` element.
  // `playbackEpochRef` is bumped by `stopPlayback` so an in-flight
  // synthesize-or-play continuation started under a now-stale epoch aborts
  // itself instead of resurrecting audio for a turn that was just cut off
  // (Voice Layer Plan §6 risk: "audio outliving its turn").
  const turnBufferRef = useRef("");
  const synthQueueRef = useRef<string[]>([]);
  const drainingRef = useRef(false);
  const playbackEpochRef = useRef(0);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const currentAudioUrlRef = useRef<string | null>(null);
  // Resolves the in-flight `playOne` promise — `stopPlayback` calls this
  // directly, since `audio.pause()` alone never fires `onended` and would
  // otherwise leave `drainQueue`'s loop awaiting a promise that never
  // settles.
  const currentResolveRef = useRef<(() => void) | null>(null);
  // Set by `stopPlayback` (✕ Stop, or a barge-in) and cleared by the next
  // `beginVoiceTurn` — suppresses further sentences from a turn that's
  // still streaming after it was cut off, so it doesn't quietly start
  // speaking again a moment later.
  const voiceMutedRef = useRef(false);

  function playOne(audioBytes: ArrayBuffer, epoch: number): Promise<void> {
    return new Promise<void>((resolve) => {
      if (playbackEpochRef.current !== epoch) {
        resolve();
        return;
      }
      const url = URL.createObjectURL(new Blob([audioBytes], { type: "audio/wav" }));
      const audio = new Audio(url);
      currentAudioRef.current = audio;
      currentAudioUrlRef.current = url;
      currentResolveRef.current = resolve;
      const finish = () => {
        URL.revokeObjectURL(url);
        currentAudioRef.current = null;
        currentAudioUrlRef.current = null;
        currentResolveRef.current = null;
        resolve();
      };
      audio.onended = finish;
      audio.onerror = finish;
      void audio.play().catch(finish);
    });
  }

  async function drainQueue(): Promise<void> {
    if (drainingRef.current) return;
    drainingRef.current = true;
    const epoch = playbackEpochRef.current;
    try {
      while (synthQueueRef.current.length > 0) {
        if (playbackEpochRef.current !== epoch) return;
        const sentence = synthQueueRef.current.shift() as string;
        let audioBytes: ArrayBuffer;
        try {
          audioBytes = await postJsonForBinary("/api/voice/synthesize", { text: sentence });
        } catch (err) {
          // Rules.md: "partial source failure degrades, it does not fail" —
          // one sentence's synthesis failing skips just that sentence,
          // the rest of the turn keeps speaking.
          setError(err instanceof Error ? err.message : "Could not synthesize this reply");
          continue;
        }
        if (playbackEpochRef.current !== epoch) return;
        await playOne(audioBytes, epoch);
      }
    } finally {
      drainingRef.current = false;
    }
  }

  /** Enqueues one cleaned sentence for synthesis+playback, in order. */
  function enqueueSentence(cleaned: string): void {
    if (!cleaned) return;
    synthQueueRef.current.push(cleaned);
    void drainQueue();
  }

  /** Clears the queue and cuts whatever is currently playing (✕ Stop, or a
   * barge-in) — mutes the rest of the turn that was playing so a still-
   * streaming interrupted turn doesn't start speaking again a moment later. */
  function stopPlayback(): void {
    playbackEpochRef.current += 1;
    voiceMutedRef.current = true;
    synthQueueRef.current = [];
    currentAudioRef.current?.pause();
    if (currentAudioUrlRef.current) URL.revokeObjectURL(currentAudioUrlRef.current);
    currentAudioRef.current = null;
    currentAudioUrlRef.current = null;
    currentResolveRef.current?.();
    currentResolveRef.current = null;
  }

  /** Call when a voice-tagged send goes out — resets the per-turn buffer
   * and unmutes (a fresh turn is not the one a previous barge-in cut off). */
  function beginVoiceTurn(): void {
    stopPlayback();
    voiceMutedRef.current = false;
    turnBufferRef.current = "";
  }

  /** Call on every `text_delta` while the current turn is voice-tagged —
   * extracts and speaks whatever complete sentences the new text
   * completes, holding back an in-progress one (V7: heard as it's written,
   * not held until the turn finishes). */
  function feedAssistantDelta(delta: string): void {
    if (voiceMutedRef.current) return;
    turnBufferRef.current += delta;
    const { sentences, remainder } = splitCompleteSentences(turnBufferRef.current);
    turnBufferRef.current = remainder;
    for (const sentence of sentences) enqueueSentence(cleanForSpeech(sentence));
  }

  /** Call on the voice-tagged turn's `turn_complete` — speaks whatever text
   * never reached a sentence terminator (e.g. the reply's last clause). */
  function endVoiceTurn(): void {
    const remainder = turnBufferRef.current;
    turnBufferRef.current = "";
    if (voiceMutedRef.current) return;
    const cleaned = cleanForSpeech(remainder);
    if (cleaned) enqueueSentence(cleaned);
  }

  /** Cancels a still-pending chord-triggered send (the status line's Undo
   * affordance) — the misheard/unwanted transcript never reaches the agent. */
  function cancelPendingVoiceMessage(): void {
    if (pendingTimerRef.current !== null) {
      clearTimeout(pendingTimerRef.current);
      pendingTimerRef.current = null;
    }
    setPendingVoiceMessage(null);
  }

  async function handleChordRelease(): Promise<void> {
    const text = await transcribeOnRelease();
    if (!text) return;
    setPendingVoiceMessage(text);
    pendingTimerRef.current = setTimeout(() => {
      pendingTimerRef.current = null;
      setPendingVoiceMessage(null);
      onSendRef.current(text);
    }, CANCEL_WINDOW_MS);
  }

  useEffect(() => {
    return () => {
      if (pendingTimerRef.current !== null) clearTimeout(pendingTimerRef.current);
      if (liveCaptionTimerRef.current !== null) clearInterval(liveCaptionTimerRef.current);
      stopPlayback();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Talk-key state machine (MODULES.md's specified but never-built key
  // machine) — reads the binding from Settings (`usePttBinding`), not a
  // hardcoded chord (V12). Window-level listeners so the chord works
  // anywhere in the app, not just while an input has focus; `CompanionPane`
  // stays mounted on every screen (D32), so attaching this here already
  // covers "anywhere".
  const pttBindingQuery = usePttBinding();
  const chordKeysRef = useRef<ReadonlySet<ModifierKey>>(new Set());
  chordKeysRef.current = pttBindingQuery.data ? keysForBinding(pttBindingQuery.data) : new Set();
  const heldRef = useRef<Set<ModifierKey>>(new Set());
  // True once every key in the chord has been seen held together — guards
  // against a keyup for a key that was never part of an active hold (e.g.
  // Shift released alone before Ctrl ever joined it) ending a hold that
  // never started, and against key-repeat's synthetic keydowns re-starting
  // one already in progress.
  const chordActiveRef = useRef(false);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (!isModifierKey(event.key) || event.repeat) return;
      const chordKeys = chordKeysRef.current;
      if (chordKeys.size === 0 || !chordKeys.has(event.key)) return;
      heldRef.current.add(event.key);
      if (chordActiveRef.current || recording) return;
      const allHeld = [...chordKeys].every((key) => heldRef.current.has(key));
      if (allHeld) {
        chordActiveRef.current = true;
        // V7: holding the talk key barges in and cuts whatever is playing —
        // the same trigger as starting a new recording, since pressing the
        // chord at all already means "I want to talk now."
        stopPlayback();
        void startRecording();
      }
    }

    function handleKeyUp(event: KeyboardEvent) {
      if (!isModifierKey(event.key)) return;
      heldRef.current.delete(event.key);
      if (!chordActiveRef.current || !chordKeysRef.current.has(event.key)) return;
      // Releasing any one key of an active chord ends the hold — mirrors
      // the mic button's own onMouseLeave/onMouseUp treatment of "the hold
      // stopped" rather than requiring every key released simultaneously.
      chordActiveRef.current = false;
      void handleChordRelease();
    }

    // A window blur (Alt-Tab away, focus leaving the renderer) drops any
    // modifiers physically still held from this app's point of view — same
    // failure mode `ResizeHandle`'s own pointer-capture cleanup guards
    // against, just for keys instead of a pointer drag.
    function handleBlur() {
      heldRef.current.clear();
      if (chordActiveRef.current) {
        chordActiveRef.current = false;
        void handleChordRelease();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);
    window.addEventListener("blur", handleBlur);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
      window.removeEventListener("blur", handleBlur);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recording]);

  return {
    recording,
    error,
    startRecording,
    transcribeOnRelease,
    liveCaption,
    pendingVoiceMessage,
    cancelPendingVoiceMessage,
    beginVoiceTurn,
    feedAssistantDelta,
    endVoiceTurn,
    stopPlayback,
  };
}
