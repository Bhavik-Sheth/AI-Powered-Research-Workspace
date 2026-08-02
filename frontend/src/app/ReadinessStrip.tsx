import { useReadiness } from "../state/useReadiness";

const CAPABILITY_LABEL: Record<string, string> = {
  vault: "Vault",
  database: "Database",
  docker: "Docker",
  llm: "LLM",
  search: "Search",
  embeddings: "Embeddings",
  reranker: "Reranker",
  voice: "Voice",
};

/** Turns each capability green as `GET /api/health` reports it (Phase 1.1). */
export function ReadinessStrip() {
  const { data, isPending, isError } = useReadiness();

  if (isPending) {
    return <div className="readiness-strip readiness-strip--loading">Connecting to sidecar…</div>;
  }
  if (isError || !data) {
    return <div className="readiness-strip readiness-strip--error">Sidecar unreachable</div>;
  }

  return (
    <ul className="readiness-strip">
      {Object.entries(data.capabilities).map(([capability, state]) => (
        <li key={capability} className={`readiness-strip__item readiness-strip__item--${state}`}>
          {CAPABILITY_LABEL[capability] ?? capability}
        </li>
      ))}
    </ul>
  );
}
