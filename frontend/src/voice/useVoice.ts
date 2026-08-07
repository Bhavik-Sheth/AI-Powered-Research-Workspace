import { useRef, useState } from "react";

import { postBinaryForJson, postJsonForBinary } from "../state/bridge";

/** Voice Capture (MODULES.md, D37) — the only module touching
 * `getUserMedia` or an audio element. Push-to-talk: hold to record, release
 * to transcribe via the stub engine; a spoken turn then sends through the
 * identical Companion path as a typed one, tagged `input_modality: "voice"`.
 */
export function useVoice() {
  const [recording, setRecording] = useState(false);
  // A denied microphone permission (or any other capture/transcribe/synthesize
  // failure) lands here instead of an unhandled rejection, so the caller can
  // disable push-to-talk with an explanatory tooltip rather than a silent
  // no-op (MODULES.md Voice Capture: Errors).
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

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

  return { recording, error, startRecording, transcribeOnRelease, playAudio };
}
