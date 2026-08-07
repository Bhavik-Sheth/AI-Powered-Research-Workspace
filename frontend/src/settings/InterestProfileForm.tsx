import { useEffect, useRef, useState } from "react";
import {
  getInterestProfileApiProjectsProjectIdInterestProfileGet,
  putInterestProfileApiProjectsProjectIdInterestProfilePut,
  type InterestProfile,
} from "@research-os/api-client";

import { ErrorCard } from "../design/ErrorCard";
import "./InterestProfileForm.css";

function TagList({
  label,
  placeholder,
  values,
  onChange,
}: {
  label: string;
  placeholder: string;
  values: string[];
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  function add() {
    const value = draft.trim();
    if (value && !values.includes(value)) onChange([...values, value]);
    setDraft("");
  }

  return (
    <label>
      {label}
      <div className="interest-profile__tags">
        {values.map((value) => (
          <span key={value} className="interest-profile__tag">
            {value}
            <button type="button" onClick={() => onChange(values.filter((v) => v !== value))} aria-label={`Remove ${value}`}>
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="interest-profile__tag-input">
        <input
          value={draft}
          placeholder={placeholder}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              add();
            }
          }}
        />
        <button type="button" onClick={add} disabled={!draft.trim()}>
          + Add
        </button>
      </div>
    </label>
  );
}

/** Interest Profile settings (MODULES.md Research Feed, D28) — the project's
 * `{categories, keywords}`, inspectable and user-editable, seeded from the
 * project's focus seed the first time it's read. */
export function InterestProfileForm({ projectId }: { projectId: string }) {
  const [profile, setProfile] = useState<InterestProfile | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The exact change a failed save attempted — `profile` itself is rolled
  // back to `previous` on failure (below), so Retry needs this to redo the
  // same edit rather than harmlessly re-saving the already-rolled-back value.
  const failedSaveRef = useRef<InterestProfile | null>(null);

  async function load() {
    setError(null);
    try {
      const { data } = await getInterestProfileApiProjectsProjectIdInterestProfileGet({
        path: { project_id: projectId },
        throwOnError: true,
      });
      setProfile(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the interest profile");
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function save(next: InterestProfile) {
    // Optimistic update — kept only if the PUT below actually succeeds; a
    // failure rolls the form back to `previous` so the UI never shows a
    // change that didn't persist.
    const previous = profile;
    setProfile(next);
    setSaving(true);
    setError(null);
    try {
      await putInterestProfileApiProjectsProjectIdInterestProfilePut({
        path: { project_id: projectId },
        body: next,
        throwOnError: true,
      });
      failedSaveRef.current = null;
    } catch (err) {
      setProfile(previous);
      failedSaveRef.current = next;
      setError(err instanceof Error ? err.message : "Could not save the interest profile");
    } finally {
      setSaving(false);
    }
  }

  if (error && !profile) return <ErrorCard title="Could not load the interest profile" message={error} onRetry={load} />;
  if (!profile) return <p>Loading…</p>;

  return (
    <div className="interest-profile">
      <p className="interest-profile__hint">
        Papers surfaced in the Feed are matched against these categories and keywords. Edit them any time — changes
        take effect on the next catch-up poll.
      </p>
      <TagList
        label="Categories (e.g. arXiv codes like cs.CL, cs.LG)"
        placeholder="cs.CL"
        values={profile.categories ?? []}
        onChange={(categories) => void save({ ...profile, categories })}
      />
      <TagList
        label="Keywords"
        placeholder="retrieval-augmented generation"
        values={profile.keywords ?? []}
        onChange={(keywords) => void save({ ...profile, keywords })}
      />
      {saving && <p className="interest-profile__status">Saving…</p>}
      {error && (
        <ErrorCard
          title="Could not save the interest profile"
          message={error}
          onRetry={() => void save(failedSaveRef.current ?? profile)}
        />
      )}
    </div>
  );
}
