import { useEffect, useState } from "react";
import {
  listProjectPapersApiProjectsProjectIdPapersGet,
  patchProjectPaperApiProjectsProjectIdPapersPaperIdPatch,
  reprocessPaperApiProjectsProjectIdPapersPaperIdReprocessPost,
  type LibraryEntry,
  type Paper,
} from "@research-os/api-client";

import { ErrorCard } from "../design/ErrorCard";
import "./LibraryView.css";

type Relevance = "relevant" | "somewhat" | "not" | "unset";
const RELEVANCE_LABEL: Record<Relevance, string> = {
  relevant: "Relevant",
  somewhat: "Somewhat",
  not: "Not relevant",
  unset: "Unmarked",
};
const RELEVANCE_VALUES: Relevance[] = ["relevant", "somewhat", "not", "unset"];

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

/** Library View (MODULES.md) — the project's papers, relevance control, processing badges. */
export function LibraryView({
  projectId,
  onOpenPaper,
}: {
  projectId: string;
  onOpenPaper: (paperId: string, title: string) => void;
}) {
  const [entries, setEntries] = useState<LibraryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      const { data } = await listProjectPapersApiProjectsProjectIdPapersGet({
        path: { project_id: projectId },
        throwOnError: true,
      });
      setEntries(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the library");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function setRelevance(paperId: string, relevance: Relevance) {
    try {
      await patchProjectPaperApiProjectsProjectIdPapersPaperIdPatch({
        path: { project_id: projectId, paper_id: paperId },
        body: { relevance },
        throwOnError: true,
      });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update relevance for this paper");
    }
  }

  async function retry(paperId: string) {
    try {
      await reprocessPaperApiProjectsProjectIdPapersPaperIdReprocessPost({
        path: { project_id: projectId, paper_id: paperId },
        throwOnError: true,
      });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not retry processing this paper");
    }
  }

  if (loading) return <p>Loading…</p>;
  if (error && entries.length === 0) {
    return <ErrorCard title="Could not load the library" message={error} onRetry={refresh} />;
  }

  return (
    <div className="library">
      {error && <ErrorCard title="Something went wrong" message={error} onRetry={refresh} />}
      {entries.map(({ paper, relevance }) => (
        <div className="library__row" key={paper.id}>
          <button type="button" className="library__title" onClick={() => onOpenPaper(paper.id, paper.title)}>
            {paper.title}
          </button>
          <div className="library__stages">
            {STAGES.map(({ key, label }) => {
              const state = paper[key] as string;
              return (
                <span key={key} className={`library__badge ${state === "done" ? "library__badge--done" : ""} ${state === "failed" ? "library__badge--failed" : ""}`}>
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
          <div className="library__relevance">
            {RELEVANCE_VALUES.map((value) => (
              <button
                key={value}
                type="button"
                className={relevance === value ? "library__relevance--active" : ""}
                onClick={() => void setRelevance(paper.id, value)}
              >
                {RELEVANCE_LABEL[value]}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
