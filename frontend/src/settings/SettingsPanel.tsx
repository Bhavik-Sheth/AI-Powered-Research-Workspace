import { useEffect, useState } from "react";
import {
  getModelsApiSettingsModelsGet,
  putModelBudgetApiSettingsModelsBudgetPut,
  type ModelSettings,
  type SaveModelBudgetRequest,
  type SaveProviderRequest,
} from "@research-os/api-client";

import "../onboarding/wizard.css";
import { ErrorCard } from "../design/ErrorCard";
import { providerLabel } from "../design/labels";
import { InterestProfileForm } from "./InterestProfileForm";
import { ProviderForm } from "./ProviderForm";
import "./SettingsPanel.css";
import { VoiceSettingsForm } from "./VoiceSettingsForm";

type Tier = SaveProviderRequest["credentials"]["tier"];
type BudgetProvider = SaveModelBudgetRequest["provider"];

/** Splits a stored `"provider/model"` string the same way `settings/__init__.py`'s
 * `save_provider` builds it (`model_string.partition("/")` on the backend) — the
 * model half can itself contain `/` (e.g. an OpenRouter id), so only the first
 * segment is the provider. */
function splitModelString(modelString: string): { provider: BudgetProvider; model: string } {
  const [provider, ...rest] = modelString.split("/");
  return { provider: provider as BudgetProvider, model: rest.join("/") };
}

/**
 * Shows and lets the user override `model_budgets[f"{provider}/{model}"]`
 * (Bug Fix Plan Phase 2.2) for whichever model a tier currently points at —
 * the same map LLM Gateway's rate-limit self-heal (Phase 2.1) writes
 * automatically, so a known free-tier ceiling can be entered before the
 * first failure, and an already-learned one is auditable rather than
 * invisible. `splitModelString` sends the bare model name to the route
 * (matching `SaveModelBudgetRequest.model`); the backend rejoins it with
 * `provider` before writing, so the read here must key by the full
 * `modelString`, not the bare model half. Reuses the click-to-edit
 * affordance `ModelTierEditor` uses for the model itself.
 */
function ModelBudgetEditor({
  modelString,
  settings,
  onSaved,
}: {
  modelString: string;
  settings: ModelSettings;
  onSaved: () => void;
}) {
  const { provider, model } = splitModelString(modelString);
  const currentBudget = settings.providers[provider]?.model_budgets?.[modelString];

  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(currentBudget != null ? String(currentBudget) : "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    const budget = Number(value);
    if (!Number.isInteger(budget) || budget <= 0) {
      setError("Enter a whole number of tokens greater than zero");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await putModelBudgetApiSettingsModelsBudgetPut({
        body: { provider, model, budget },
        throwOnError: true,
      });
      setEditing(false);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save this budget");
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <p className="settings-panel__budget">
        <strong>Request-token budget:</strong>{" "}
        {currentBudget != null
          ? `${currentBudget.toLocaleString()} tokens`
          : "not set — will be learned automatically on first rate limit"}{" "}
        <button type="button" className="wizard__button--secondary" onClick={() => setEditing(true)}>
          {currentBudget != null ? "Change" : "Set"}
        </button>
      </p>
    );
  }

  return (
    <div className="settings-panel__editor">
      <label>
        Request-token budget for {modelString}
        <input
          type="number"
          min={1}
          step={1}
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
      </label>
      {error && <ErrorCard title="Could not save this budget" message={error} onRetry={handleSave} />}
      <div className="wizard__actions">
        <button
          type="button"
          className="wizard__button--secondary"
          onClick={() => {
            setEditing(false);
            setError(null);
            setValue(currentBudget != null ? String(currentBudget) : "");
          }}
        >
          Cancel
        </button>
        <button type="button" className="wizard__button" onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

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
                {settings.primary_model && (
                  <ModelBudgetEditor modelString={settings.primary_model} settings={settings} onSaved={load} />
                )}
                <ModelTierEditor tier="primary" label="primary model" onSaved={load} />
              </div>
              <div>
                <h3>Auxiliary model</h3>
                <p className="settings-panel__hint">
                  Used for extraction, summarisation and interest classification. Falls back to the primary model
                  when unset.
                </p>
                {settings.auxiliary_model && (
                  <ModelBudgetEditor modelString={settings.auxiliary_model} settings={settings} onSaved={load} />
                )}
                <ModelTierEditor tier="auxiliary" label="auxiliary model" onSaved={load} />
              </div>
            </div>
          </>
        )}
      </section>
      <section className="settings-panel__section">
        <h2>Voice</h2>
        <VoiceSettingsForm />
      </section>
      <section className="settings-panel__section">
        <h2>Interest profile</h2>
        <InterestProfileForm projectId={projectId} />
      </section>
    </div>
  );
}
