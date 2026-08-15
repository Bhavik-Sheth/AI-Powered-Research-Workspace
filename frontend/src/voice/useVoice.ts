import { useEffect, useRef, useState } from "react";

import { usePttBinding } from "../state/usePttBinding";
import { postBinaryForJson, postJsonForBinary } from "../state/bridge";
import { isModifierKey, keysForBinding, type ModifierKey } from "./pttBinding";

// Voice Layer Plan V13: long enough to read and cancel a short misheard
// utterance, short enough that a turn the user actually meant to send
// isn't kept waiting.
const CANCEL_WINDOW_MS = 2000;

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

  async function startRecording(): Promise<void> {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.start();
      recorderRef.current = recorder;
      setRecording(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not access the microphone");
    }
  }

  function stopRecording(): Promise<Blob | null> {
    return new Promise((resolve) => {
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

  async function playAudio(text: string): Promise<void> {
    setError(null);
    try {
      const audioBytes = await postJsonForBinary("/api/voice/synthesize", { text });
      const url = URL.createObjectURL(new Blob([audioBytes], { type: "audio/wav" }));
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      await audio.play();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not play this audio");
    }
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
    };
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
    playAudio,
    pendingVoiceMessage,
    cancelPendingVoiceMessage,
  };
}
