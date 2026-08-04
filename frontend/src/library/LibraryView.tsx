import { useEffect, useState } from "react";
import {
  listProjectPapersApiProjectsProjectIdPapersGet,
  patchProjectPaperApiProjectsProjectIdPapersPaperIdPatch,
  reprocessPaperApiProjectsProjectIdPapersPaperIdReprocessPost,
  type LibraryEntry,
  type Paper,
} from "@research-os/api-client";

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

  async function refresh() {
    const { data } = await listProjectPapersApiProjectsProjectIdPapersGet({
      path: { project_id: projectId },
      throwOnError: true,
    });
    setEntries(data);
    setLoading(false);
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function setRelevance(paperId: string, relevance: Relevance) {
    await patchProjectPaperApiProjectsProjectIdPapersPaperIdPatch({
      path: { project_id: projectId, paper_id: paperId },
      body: { relevance },
      throwOnError: true,
    });
    await refresh();
  }

  async function retry(paperId: string) {
    await reprocessPaperApiProjectsProjectIdPapersPaperIdReprocessPost({
      path: { project_id: projectId, paper_id: paperId },
      throwOnError: true,
    });
    await refresh();
  }

  if (loading) return <p>Loading…</p>;

  return (
    <div className="library">
      {entries.map(({ paper, relevance }) => (
        <div className="library__row" key={paper.id}>
          <span className="library__title" onClick={() => onOpenPaper(paper.id, paper.title)} style={{ cursor: "pointer" }}>
            {paper.title}
          </span>
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
