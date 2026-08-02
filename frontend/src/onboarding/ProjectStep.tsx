import { useState } from "react";
import { createProjectApiProjectsPost, putOnboardingCompleteApiSettingsOnboardingCompletePut } from "@research-os/api-client";

import { ErrorCard } from "../design/ErrorCard";

/** Onboarding step 4 (D35) — the focus seed is optional and skippable. */
export function ProjectStep({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [focusSeed, setFocusSeed] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCreate() {
    setSaving(true);
    setError(null);
    try {
      await createProjectApiProjectsPost({
        body: { name, focus_seed: focusSeed || null },
        throwOnError: true,
      });
      await putOnboardingCompleteApiSettingsOnboardingCompletePut({ throwOnError: true });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the project");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <label>
        Project name
        <input value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Attention Sinks" />
      </label>
      <label>
        Focus seed (optional)
        <input
          value={focusSeed}
          onChange={(event) => setFocusSeed(event.target.value)}
          placeholder="A sentence about what you're researching"
        />
      </label>
      {error && <ErrorCard title="Could not create the project" message={error} onRetry={handleCreate} />}
      <div className="wizard__actions">
        <span />
        <button type="button" className="wizard__button" onClick={handleCreate} disabled={saving || !name}>
          {saving ? "Creating…" : "Create project"}
        </button>
      </div>
    </div>
  );
}
