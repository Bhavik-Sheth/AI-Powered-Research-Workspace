import { useState } from "react";
import { putVaultPathApiSettingsVaultPathPut } from "@research-os/api-client";

import { ErrorCard } from "../design/ErrorCard";

const DEFAULT_VAULT_PATH = "~/ResearchOS";

/** Onboarding step 2 (D3/D35) — not skippable; defaults to ~/ResearchOS. */
export function VaultStep({ onNext }: { onNext: () => void }) {
  const [path, setPath] = useState(DEFAULT_VAULT_PATH);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleContinue() {
    setSaving(true);
    setError(null);
    try {
      await putVaultPathApiSettingsVaultPathPut({ body: { path }, throwOnError: true });
      onNext();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the vault path");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <label>
        Vault folder
        <input value={path} onChange={(event) => setPath(event.target.value)} />
      </label>
      {error && <ErrorCard title="Could not set the vault path" message={error} onRetry={handleContinue} />}
      <div className="wizard__actions">
        <span />
        <button type="button" className="wizard__button" onClick={handleContinue} disabled={saving || !path}>
          {saving ? "Saving…" : "Continue"}
        </button>
      </div>
    </div>
  );
}
