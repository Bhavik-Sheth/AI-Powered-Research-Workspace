import { useEffect, useRef, useState } from "react";
import {
  addPaperApiProjectsProjectIdPapersPost,
  getResultApiResultsResultIdGet,
  postSearchApiSearchPost,
  type PaperSummary,
  type ResultSet,
} from "@research-os/api-client";

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
 *
 * `initialResultId`, when set, loads an already-cached result set instead
 * of requiring a fresh query — the Companion's `search_papers` tool opens
 * this same view onto the result it already produced (Bug Fix Plan
 * Phase 2.3), rather than making the user re-type the search.
 *
 * `initialQuery`, when set, runs a fresh query as soon as it arrives — the
 * top bar's global search field (UI_DESIGN.md §2, Phase 5.4) opens this
 * same tab rather than a second, parallel search mechanism. It carries a
 * `nonce` (mirroring `CompanionPane`'s own `PendingAsk`) so a second search
 * for the same text still re-fires even though this tab stays mounted
 * between activations.
 */
export function SearchResults({
  projectId,
  onAdded,
  initialResultId,
  initialQuery,
}: {
  projectId: string;
  onAdded?: (paperId: string) => void;
  initialResultId?: string;
  initialQuery?: { text: string; nonce: number } | null;
}) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<ResultSet | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addedIds, setAddedIds] = useState<Set<string>>(new Set());
  const [addingId, setAddingId] = useState<string | null>(null);
  // Aborts a still-in-flight search when a newer one starts, so a slower
  // earlier response can never land after (and overwrite) a faster later
  // one — the generated client's request `Options` extend `RequestInit`,
  // so `signal` is a real, typed option here (Rules.md Code Generation:
  // an AbortController is only preferred once this is verified).
  const searchAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!initialResultId) return;
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const { data } = await getResultApiResultsResultIdGet({ path: { result_id: initialResultId }, throwOnError: true });
        if (!cancelled) setResult(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load this result set");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [initialResultId]);

  useEffect(() => {
    if (!initialQuery) return;
    setQuery(initialQuery.text);
    void handleSearch(initialQuery.text);
    // `nonce` is the trigger, not the text — a repeat search for the same
    // text still needs to re-fire (see the doc comment above).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQuery?.nonce]);

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

  async function handleSearch(queryOverride?: string) {
    // `queryOverride` lets a caller that just set `query` via `setQuery`
    // (state updates aren't synchronous) search the text it actually meant
    // instead of racing the still-stale `query` closure value.
    const effectiveQuery = queryOverride ?? query;
    if (!effectiveQuery.trim()) return;
    // A previous search still in flight is superseded, not raced against:
    // abort it so its response — if it ever arrives — can't land after
    // this one and clobber it.
    searchAbortRef.current?.abort();
    const controller = new AbortController();
    searchAbortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const { data } = await postSearchApiSearchPost({
        body: { query: effectiveQuery },
        signal: controller.signal,
        throwOnError: true,
      });
      if (controller.signal.aborted) return;
      setResult(data);
    } catch (err) {
      if (controller.signal.aborted) return; // superseded by a newer search, not a real failure
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      if (searchAbortRef.current === controller) setLoading(false);
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
        <button type="button" className="search__submit" onClick={() => handleSearch()} disabled={loading}>
          Search
        </button>
      </div>

      {loading && (
        <div className="search__progress">
          <span className="search__spinner" />
          Searching arXiv, OpenAlex, Semantic Scholar…
        </div>
      )}

      {error && <ErrorCard title="Search failed" message={error} onRetry={() => handleSearch()} />}

      {result && (result.sources_failed ?? []).length > 0 && (
        <ErrorCard
          title={`${(result.sources_failed ?? []).map((s) => SOURCE_LABEL[s] ?? s).join(", ")} did not respond`}
          message={
            (result.sources_failed ?? []).length < 3
              ? "Other sources returned normally — these results are incomplete."
              : "No sources responded."
          }
          onRetry={() => handleSearch()}
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
