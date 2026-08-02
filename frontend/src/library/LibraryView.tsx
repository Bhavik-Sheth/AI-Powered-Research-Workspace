import { useEffect, useState } from "react";
import {
  listProjectPapersApiProjectsProjectIdPapersGet,
  patchProjectPaperApiProjectsProjectIdPapersPaperIdPatch,
  type LibraryEntry,
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

/** Library View (MODULES.md) — the project's papers, relevance control, processing badges. */
export function LibraryView({ projectId, onOpenPaper }: { projectId: string; onOpenPaper: (paperId: string) => void }) {
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

  if (loading) return <p>Loading…</p>;

  return (
    <div className="library">
      {entries.map(({ paper, relevance }) => (
        <div className="library__row" key={paper.id}>
          <span className="library__title" onClick={() => onOpenPaper(paper.id)} style={{ cursor: "pointer" }}>
            {paper.title}
          </span>
          <span className={`library__badge ${paper.extract_state === "done" ? "library__badge--done" : ""}`}>
            {STATE_LABEL[paper.extract_state] ?? paper.extract_state}
          </span>
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
