import { useBackgroundActivity } from "../state/backgroundActivity";
import "./BackgroundActivityIndicator.css";

/**
 * Top-right "something is happening in the background" badge (Bug Fix Plan
 * Phase 6.12) — lit whenever any API request is in flight (search, add to
 * library, paper processing polls, ...), so a slow free-tier API call
 * (arXiv/S2/OpenAlex/Groq all rate-limit and can take real time even with
 * the new backoff) reads as "still working" instead of "did this register
 * my click at all?" `useBackgroundActivity` is a single global counter fed
 * by the API client's own interceptors (`state/bridge.ts`) — this
 * component is the one place that reads it.
 */
export function BackgroundActivityIndicator() {
  const active = useBackgroundActivity();
  if (!active) return null;
  return (
    <span className="bg-activity" role="status" aria-live="polite" title="Working in the background…">
      <span className="bg-activity__spinner" aria-hidden="true" />
      Working…
    </span>
  );
}
