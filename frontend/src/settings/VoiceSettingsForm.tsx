import { useEffect, useRef, useState } from "react";
import {
  getVoiceSettingsApiSettingsVoiceGet,
  putVoiceSettingsApiSettingsVoicePut,
  type VoiceSettings,
} from "@research-os/api-client";

import { ErrorCard } from "../design/ErrorCard";
import { VOICE_ENGINE_VALUES, type VoiceEngine, voiceEngineLabel } from "../design/labels";
import { bindingFromHeld, isModifierKey, type ModifierKey } from "../voice/pttBinding";
import "./SettingsPanel.css";

/**
 * Captures a held modifier chord for the push-to-talk binding (V12) —
 * "Rebind" starts listening; every Ctrl/Shift/Alt/Meta pressed joins the
 * preview, and releasing the last held modifier commits it. Modifier-only
 * by construction: a non-modifier keydown while capturing is ignored rather
 * than accepted, so the result can never collide with typed text (matching
 * the binding's own contract, not just its default).
 */
function PttBindingCapture({ value, onChange }: { value: string; onChange: (binding: string) => void }) {
  const [capturing, setCapturing] = useState(false);
  const [preview, setPreview] = useState<Set<ModifierKey>>(new Set());
  // Currently-held modifiers, tracked outside render state — only used to
  // know when the last one releases, never displayed itself (`preview`,
  // the fullest combination reached this capture, is what's shown and what
  // gets committed).
  const heldRef = useRef<Set<ModifierKey>>(new Set());

  function startCapture() {
    heldRef.current = new Set();
    setPreview(new Set());
    setCapturing(true);
  }

  function handleKeyDown(event: React.KeyboardEvent) {
    if (!capturing || !isModifierKey(event.key)) return;
    event.preventDefault();
    heldRef.current.add(event.key);
    setPreview(new Set(heldRef.current));
  }

  function handleKeyUp(event: React.KeyboardEvent) {
    if (!capturing || !isModifierKey(event.key)) return;
    event.preventDefault();
    heldRef.current.delete(event.key);
    if (heldRef.current.size === 0 && preview.size > 0) {
      setCapturing(false);
      onChange(bindingFromHeld(preview));
    }
  }

  return (
    <div
      className="settings-panel__ptt-capture"
      tabIndex={0}
      onKeyDown={handleKeyDown}
      onKeyUp={handleKeyUp}
      onBlur={() => capturing && setCapturing(false)}
    >
      <code>{capturing ? bindingFromHeld(preview) || "Hold the new keys…" : value}</code>
      <button type="button" className="wizard__button--secondary" onClick={startCapture}>
        {capturing ? "Listening…" : "Rebind"}
      </button>
    </div>
  );
}

/**
 * Voice section (Voice Layer Plan V10/V12) — engine profile selector and
 * the rebindable push-to-talk chord. `set_voice_engine` (Settings Store)
 * gets its first caller here; a save takes effect immediately, no restart
 * (`api/settings.py` invalidates the in-process engine cache on the same
 * request that saves it).
 */
export function VoiceSettingsForm() {
  const [settings, setSettings] = useState<VoiceSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    setError(null);
    try {
      const { data } = await getVoiceSettingsApiSettingsVoiceGet({ throwOnError: true });
      setSettings(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load voice settings");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function save(body: { voice_engine?: VoiceEngine; voice_ptt_binding?: string }) {
    setSaving(true);
    setError(null);
    try {
      const { data } = await putVoiceSettingsApiSettingsVoicePut({ body, throwOnError: true });
      setSettings(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save voice settings");
    } finally {
      setSaving(false);
    }
  }

  if (!settings) {
    return error ? <ErrorCard title="Could not load voice settings" message={error} onRetry={load} /> : null;
  }

  // `whisper_cpp` is a real CHECK value (Schema.md) with no registered
  // engine (V11) and never appears in practice — falls back to the one real
  // profile rather than leaving the <select> on a value it doesn't offer.
  const selectedEngine: VoiceEngine = settings.voice_engine === "whisper_cpp" ? "faster_whisper" : settings.voice_engine;

  return (
    <div className="settings-panel__editors">
      <div>
        <h3>Voice engine</h3>
        <label>
          <select
            value={selectedEngine}
            disabled={saving}
            onChange={(event) => void save({ voice_engine: event.target.value as VoiceEngine })}
          >
            {VOICE_ENGINE_VALUES.map((engine) => (
              <option key={engine} value={engine}>
                {voiceEngineLabel[engine]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div>
        <h3>Push-to-talk key</h3>
        <p className="settings-panel__hint">Hold this combination anywhere in the app to talk to the Companion.</p>
        <PttBindingCapture
          value={settings.voice_ptt_binding}
          onChange={(binding) => void save({ voice_ptt_binding: binding })}
        />
      </div>
      {error && <ErrorCard title="Could not save voice settings" message={error} onRetry={load} />}
    </div>
  );
}
