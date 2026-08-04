import { useState } from "react";
import {
  discoverModelsApiSettingsModelsDiscoverPost,
  putModelsApiSettingsModelsPut,
  type SaveProviderRequest,
} from "@research-os/api-client";

import { ErrorCard } from "../design/ErrorCard";
import { LOCAL_PROVIDERS, providerLabel } from "../design/labels";

type Provider = SaveProviderRequest["provider"];
const PROVIDERS = Object.keys(providerLabel) as Provider[];

/**
 * Add-and-validate-one-provider form (D13/D35) — shared by the onboarding
 * wizard and the Settings Panel. The local-provider path never shows an API
 * key field, and models are discovered, never typed (D11).
 *
 * `tier` picks which slot this save writes to (Schema.md `api_keys.primary_model`
 * / `auxiliary_model`) — the onboarding wizard only ever sets "primary"; the
 * Settings Panel renders one instance per tier so the auxiliary model is a
 * distinct, independently-settable field (Bug Fix Plan Phase 4.4).
 */
export function ProviderForm({
  tier = "primary",
  onSaved,
}: {
  tier?: SaveProviderRequest["credentials"]["tier"];
  onSaved: () => void;
}) {
  const [provider, setProvider] = useState<Provider>("google");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [discovered, setDiscovered] = useState<string[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isLocal = LOCAL_PROVIDERS.has(provider);

  function selectProvider(next: Provider) {
    setProvider(next);
    setModel("");
    setDiscovered([]);
    setError(null);
  }

  async function handleDiscover() {
    setDiscovering(true);
    setError(null);
    try {
      const { data } = await discoverModelsApiSettingsModelsDiscoverPost({
        body: { provider: provider as "ollama" | "vllm", base_url: baseUrl },
        throwOnError: true,
      });
      setDiscovered(data.models);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the endpoint");
    } finally {
      setDiscovering(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await putModelsApiSettingsModelsPut({
        body: {
          provider,
          credentials: {
            api_key: isLocal ? null : apiKey,
            base_url: isLocal ? baseUrl : null,
            model,
            tier,
          },
        },
        throwOnError: true,
      });
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not validate this provider");
    } finally {
      setSaving(false);
    }
  }

  const canSave = model.length > 0 && (isLocal ? baseUrl.length > 0 : apiKey.length > 0);

  return (
    <div>
      <label>
        Provider
        <select value={provider} onChange={(event) => selectProvider(event.target.value as Provider)}>
          {PROVIDERS.map((p) => (
            <option key={p} value={p}>
              {providerLabel[p]}
            </option>
          ))}
        </select>
      </label>

      {isLocal ? (
        <>
          <label>
            Base URL
            <input
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="http://localhost:11434"
            />
          </label>
          <button type="button" className="wizard__button--secondary" onClick={handleDiscover} disabled={discovering || !baseUrl}>
            {discovering ? "Discovering…" : "Discover models"}
          </button>
          {discovered.length > 0 && (
            <label>
              Model
              <select value={model} onChange={(event) => setModel(event.target.value)}>
                <option value="">Select a model</option>
                {discovered.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>
          )}
        </>
      ) : (
        <>
          <label>
            API key
            <input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} />
          </label>
          {provider === "custom" && (
            <label>
              Base URL
              <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} />
            </label>
          )}
          <label>
            Model
            <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="e.g. gemini-2.5-flash" />
          </label>
        </>
      )}

      {error && <ErrorCard title="Could not save this provider" message={error} onRetry={handleSave} />}

      <div className="wizard__actions">
        <span />
        <button type="button" className="wizard__button" onClick={handleSave} disabled={saving || !canSave}>
          {saving ? "Validating…" : "Save & validate"}
        </button>
      </div>
    </div>
  );
}
