import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  listProjectPapersApiProjectsProjectIdPapersGet,
  reprocessPaperApiProjectsProjectIdPapersPaperIdReprocessPost,
  type Paper,
} from "@research-os/api-client";

import "../design/buttons.css";
import { ErrorCard } from "../design/ErrorCard";
import { relevanceLabel, RELEVANCE_VALUES, type Relevance } from "../design/labels";
import "./LibraryView.css";

const STATE_LABEL: Record<string, string> = { queued: "Queued", running: "Processing…", done: "Ready", degraded: "No PDF", failed: "Failed" };
const STAGES: Array<{ key: keyof Paper; label: string }> = [
  { key: "fetch_state", label: "Fetch" },
  { key: "parse_state", label: "Parse" },
  { key: "embed_state", label: "Embed" },
  { key: "extract_state", label: "Extract" },
];

/** A stage that will never progress on its own: a terminal failure, or a
 * `queued` stage whose prerequisite is already `done` — the signature of a
 * job that never ran (Bug Fix Plan Phase 1.3). */
function needsRetry(paper: Paper): boolean {
  if (STAGES.some(({ key }) => paper[key] === "failed")) return true;
  return (
    (paper.fetch_state === "done" && paper.parse_state === "queued") ||
    (paper.parse_state === "done" && (paper.embed_state === "queued" || paper.extract_state === "queued"))
  );
}

/** Library View (MODULES.md) — the project's papers as a card grid (§4.3):
 * relevance badges + a filter-chip row + an add-paper affordance, plus
 * processing badges. `onAddPaper` opens the Search tab, where the existing
 * search-and-add flow (SearchResults.tsx) already lives — this view never
 * duplicates that mutation. */
export function LibraryView({
  projectId,
  onOpenPaper,
  onAddPaper,
}: {
  projectId: string;
  onOpenPaper: (paperId: string, title: string) => void;
  onAddPaper: () => void;
}) {
  const queryClient = useQueryClient();
  // Shared cache key with ReaderTab's own relevance read (Frontend
  // Improvement Plan Phase 5 live-verify fix): the Reader header is the
  // only place relevance is set now (§3.2 / Phase 5.3), and tabs never
  // unmount (App Shell), so without a shared query key an already-open
  // Library tab kept showing the pre-change badge/count until a full
  // reload — invalidating this key from ReaderTab's mutation is what
  // makes the change visible without one.
  const papersQuery = useQuery({
    queryKey: ["projectPapers", projectId],
    queryFn: async () => {
      const { data } = await listProjectPapersApiProjectsProjectIdPapersGet({
        path: { project_id: projectId },
        throwOnError: true,
      });
      return data;
    },
  });
  const entries = papersQuery.data ?? [];
  const [retryError, setRetryError] = useState<string | null>(null);
  // Filter-chip selection (§3.2 "Filter chip"): null means every relevance
  // is shown, mirroring Graph's own null-means-all filter convention.
  const [activeRelevances, setActiveRelevances] = useState<Set<Relevance> | null>(null);

  async function retry(paperId: string) {
    try {
      await reprocessPaperApiProjectsProjectIdPapersPaperIdReprocessPost({
        path: { project_id: projectId, paper_id: paperId },
        throwOnError: true,
      });
      await queryClient.invalidateQueries({ queryKey: ["projectPapers", projectId] });
    } catch (err) {
      setRetryError(err instanceof Error ? err.message : "Could not retry processing this paper");
    }
  }

  const counts = useMemo(() => {
    const c: Record<Relevance, number> = { relevant: 0, somewhat: 0, not: 0, unset: 0 };
    for (const entry of entries) {
      const value = entry.relevance as Relevance;
      if (value in c) c[value] += 1;
    }
    return c;
  }, [entries]);

  function toggleFilter(value: Relevance) {
    setActiveRelevances((prev) => {
      const base = prev ?? new Set(RELEVANCE_VALUES);
      const next = new Set(base);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }

  const visibleEntries =
    activeRelevances === null ? entries : entries.filter((entry) => activeRelevances.has(entry.relevance as Relevance));

  if (papersQuery.isPending) return <p>Loading…</p>;
  if (papersQuery.isError) {
    return (
      <ErrorCard
        title="Could not load the library"
        message={papersQuery.error instanceof Error ? papersQuery.error.message : "Unknown error"}
        onRetry={() => papersQuery.refetch()}
      />
    );
  }

  return (
    <div className="library">
      <div className="library__header">
        <h1 className="library__title">Papers</h1>
        <div className="library__header-right">
          <div className="library__filters">
            {RELEVANCE_VALUES.map((value) => {
              const on = activeRelevances === null || activeRelevances.has(value);
              return (
                <button
                  key={value}
                  type="button"
                  className={`library__badge library__badge--${value} library__filter-chip ${on ? "" : "library__filter-chip--off"}`}
                  onClick={() => toggleFilter(value)}
                >
                  {relevanceLabel[value]} {counts[value]}
                </button>
              );
            })}
          </div>
          <button type="button" className="btn btn--primary" onClick={onAddPaper}>
            + Add paper
          </button>
        </div>
      </div>

      {retryError && (
        <ErrorCard
          title="Couldn't refresh"
          message={retryError}
          onRetry={() => {
            setRetryError(null);
            void papersQuery.refetch();
          }}
        />
      )}

      <div className="library__grid">
        {visibleEntries.map(({ paper, relevance }) => (
          <div className="library__card" key={paper.id}>
            <button type="button" className="library__card-title" onClick={() => onOpenPaper(paper.id, paper.title)}>
              {paper.title}
            </button>
            <div className="library__stages">
              {STAGES.map(({ key, label }) => {
                const state = paper[key] as string;
                return (
                  <span
                    key={key}
                    className={`library__stage-badge ${state === "done" ? "library__stage-badge--done" : ""} ${state === "failed" ? "library__stage-badge--failed" : ""}`}
                  >
                    {label}: {STATE_LABEL[state] ?? state}
                  </span>
                );
              })}
            </div>
            {needsRetry(paper) && (
              <button type="button" className="library__retry" onClick={() => void retry(paper.id)}>
                Retry
              </button>
            )}
            {/* Read-only badge (§3.2 "Badge" presentation) — the segmented
                control that edits this value lives only in the Reader
                header now (§4.2 / Phase 5.3), per §3.2's own presentation
                table: Library's card never edits relevance directly. */}
            <span className={`library__badge library__badge--${relevance}`}>
              {relevanceLabel[relevance as Relevance] ?? relevance}
            </span>
          </div>
        ))}
        <button type="button" className="library__import-placeholder" onClick={onAddPaper}>
          + Import from arXiv
        </button>
      </div>
    </div>
  );
}
