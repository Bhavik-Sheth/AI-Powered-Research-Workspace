import { useState } from "react";
import { addPaperApiProjectsProjectIdPapersPost, postSearchApiSearchPost, type PaperSummary, type ResultSet } from "@research-os/api-client";

import { ErrorCard } from "../design/ErrorCard";
import "./SearchResults.css";

const SOURCE_LABEL: Record<string, string> = { arxiv: "arXiv", openalex: "OpenAlex", s2: "Semantic Scholar" };

/**
 * Federated search (D20/D21, MODULES.md Search Results). Phase 1.3 ships one
 * request/response call — true per-source incremental streaming needs the
 * WebSocket tool-call event stream that lands with the Agent Harness
 * (Phase 1.5+), so this shows one loading state rather than per-source
 * progress, and renders `sources_failed` as the "what still worked" error
 * card once the response (partial or complete) arrives.
 */
export function SearchResults({ projectId, onAdded }: { projectId: string; onAdded?: (paperId: string) => void }) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<ResultSet | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addedIds, setAddedIds] = useState<Set<string>>(new Set());
  const [addingId, setAddingId] = useState<string | null>(null);

  async function handleAdd(paper: PaperSummary) {
    setAddingId(paper.canonical_id);
    try {
      const { data } = await addPaperApiProjectsProjectIdPapersPost({
        path: { project_id: projectId },
        body: {
          source_ids: paper.source_ids,
          title: paper.title,
          abstract: paper.abstract,
          source_url: paper.source_url,
          pdf_url: paper.pdf_url,
        },
        throwOnError: true,
      });
      setAddedIds((prev) => new Set(prev).add(paper.canonical_id));
      onAdded?.(data.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add this paper");
    } finally {
      setAddingId(null);
    }
  }

  async function handleSearch() {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const { data } = await postSearchApiSearchPost({ body: { query }, throwOnError: true });
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="search">
      <div className="search__box">
        <input
          className="search__input"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && handleSearch()}
          placeholder="Search everything…"
        />
        <button type="button" className="search__submit" onClick={handleSearch} disabled={loading}>
          Search
        </button>
      </div>

      {loading && (
        <div className="search__progress">
          <span className="search__spinner" />
          Searching arXiv, OpenAlex, Semantic Scholar…
        </div>
      )}

      {error && <ErrorCard title="Search failed" message={error} onRetry={handleSearch} />}

      {result && (result.sources_failed ?? []).length > 0 && (
        <ErrorCard
          title={`${(result.sources_failed ?? []).map((s) => SOURCE_LABEL[s] ?? s).join(", ")} did not respond`}
          message={
            (result.sources_failed ?? []).length < 3
              ? "Other sources returned normally — these results are incomplete."
              : "No sources responded."
          }
          onRetry={handleSearch}
        />
      )}

      {result && result.results.length === 0 && (
        <div className="search__empty">
          <p className="search__empty-title">No results for &ldquo;{result.query}&rdquo;</p>
          <p className="search__empty-body">Try different keywords, or check the source status above.</p>
        </div>
      )}

      {result && result.results.length > 0 && (
        <div className="search__grid">
          {result.results.map((paper) => {
            const added = addedIds.has(paper.canonical_id);
            const adding = addingId === paper.canonical_id;
            return (
              <article key={paper.canonical_id} className="search__card">
                <h3 className="search__card-title">{paper.title}</h3>
                <p className="search__card-meta">
                  {[(paper.authors ?? []).slice(0, 3).join(", "), paper.year, paper.venue].filter(Boolean).join(" · ")}
                  {paper.citation_count != null ? ` · ${paper.citation_count} citations` : ""}
                </p>
                {paper.abstract && <p className="search__card-abstract">{paper.abstract}</p>}
                <button
                  type="button"
                  className="search__card-add"
                  disabled={added || adding}
                  onClick={() => handleAdd(paper)}
                >
                  {added ? "Added ✓" : adding ? "Adding…" : "Add to library"}
                </button>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
