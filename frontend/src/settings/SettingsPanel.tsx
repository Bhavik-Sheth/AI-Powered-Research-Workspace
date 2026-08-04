import { useEffect, useState } from "react";
import { getModelsApiSettingsModelsGet, type ModelSettings, type SaveProviderRequest } from "@research-os/api-client";

import "../onboarding/wizard.css";
import { ErrorCard } from "../design/ErrorCard";
import { providerLabel } from "../design/labels";
import { InterestProfileForm } from "./InterestProfileForm";
import { ProviderForm } from "./ProviderForm";
import "./SettingsPanel.css";

type Tier = SaveProviderRequest["credentials"]["tier"];

function ModelSummary({ settings }: { settings: ModelSettings }) {
  const providers = Object.entries(settings.providers);
  return (
    <div className="settings-panel__summary">
      <p>
        <strong>Primary model:</strong> {settings.primary_model ?? "Not set"}
      </p>
      <p>
        <strong>Auxiliary model:</strong> {settings.auxiliary_model ?? "Not set — falls back to the primary model"}
      </p>
      {providers.length > 0 && (
        <ul className="settings-panel__providers">
          {providers.map(([name, info]) => (
            <li key={name}>
              {providerLabel[name as keyof typeof providerLabel] ?? name}
              {info.last4 && ` — key ending …${info.last4}`}
              {info.base_url && ` — ${info.base_url}`}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ModelTierEditor({ tier, label, onSaved }: { tier: Tier; label: string; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);

  if (!editing) {
    return (
      <button type="button" className="wizard__button--secondary" onClick={() => setEditing(true)}>
        Change {label}
      </button>
    );
  }

  return (
    <div className="settings-panel__editor">
      <ProviderForm
        tier={tier}
        onSaved={() => {
          setEditing(false);
          onSaved();
        }}
      />
    </div>
  );
}

/**
 * Settings tab (Bug Fix Plan Phase 4.4) — provider key, primary model and
 * auxiliary model alongside the project's interest profile, so the
 * configuration made during onboarding can be changed without re-running
 * the wizard. Reuses `ProviderForm`, the same component the onboarding
 * wizard uses (Rules.md: no duplicated interface); redaction to `…last4`
 * is enforced by the backend (Settings Store), not re-implemented here.
 */
export function SettingsPanel({ projectId }: { projectId: string }) {
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const { data } = await getModelsApiSettingsModelsGet({ throwOnError: true });
      setSettings(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load model settings");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  return (
    <div className="settings-panel">
      <section className="settings-panel__section">
        <h2>Models</h2>
        {error && <ErrorCard title="Could not load model settings" message={error} onRetry={load} />}
        {settings && (
          <>
            <ModelSummary settings={settings} />
            <div className="settings-panel__editors">
              <div>
                <h3>Primary model</h3>
                <ModelTierEditor tier="primary" label="primary model" onSaved={load} />
              </div>
              <div>
                <h3>Auxiliary model</h3>
                <p className="settings-panel__hint">
                  Used for extraction, summarisation and interest classification. Falls back to the primary model
                  when unset.
                </p>
                <ModelTierEditor tier="auxiliary" label="auxiliary model" onSaved={load} />
              </div>
            </div>
          </>
        )}
      </section>
      <section className="settings-panel__section">
        <h2>Interest profile</h2>
        <InterestProfileForm projectId={projectId} />
      </section>
    </div>
  );
}
