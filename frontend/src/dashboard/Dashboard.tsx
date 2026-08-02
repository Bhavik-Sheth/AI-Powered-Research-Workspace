import { useEffect, useState } from "react";
import { listNotesApiProjectsProjectIdNotesGet, listProjectPapersApiProjectsProjectIdPapersGet, type Note } from "@research-os/api-client";

import type { TabRef } from "../state/useTabStack";
import "./Dashboard.css";

const TAB_KIND_LABEL: Record<string, string> = {
  reader: "paper",
  library: "papers",
  notes: "notes",
  search: "search",
};

/** The project's landing view (UI_DESIGN.md §4.1) — real counts, and the
 * persisted tab stack rendered as "continue where you left off" rows
 * (Phase 1.8 sign-off: resume position is real state, not a guess). */
export function Dashboard({
  projectId,
  projectName,
  tabs,
  onResume,
}: {
  projectId: string;
  projectName: string;
  tabs: TabRef[];
  onResume: (tabId: string) => void;
}) {
  const [paperCount, setPaperCount] = useState<number | null>(null);
  const [notes, setNotes] = useState<Note[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [papers, noteList] = await Promise.all([
        listProjectPapersApiProjectsProjectIdPapersGet({ path: { project_id: projectId }, throwOnError: true }),
        listNotesApiProjectsProjectIdNotesGet({ path: { project_id: projectId }, throwOnError: true }),
      ]);
      if (cancelled) return;
      setPaperCount(papers.data.length);
      setNotes(noteList.data);
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const resumable = tabs.filter((t) => t.kind !== "dashboard");

  return (
    <div className="dashboard">
      <h1 className="dashboard__title">{projectName}</h1>

      <div className="dashboard__stats">
        <div className="dashboard__stat">
          <div className="dashboard__stat-label">Papers</div>
          <div className="dashboard__stat-value">{paperCount ?? "…"}</div>
        </div>
        <div className="dashboard__stat">
          <div className="dashboard__stat-label">Notes</div>
          <div className="dashboard__stat-value">{notes?.length ?? "…"}</div>
        </div>
      </div>

      <div className="dashboard__section">
        <h2 className="dashboard__section-label">Continue where you left off</h2>
        {resumable.length === 0 ? (
          <p className="dashboard__empty">Nothing open yet — open a paper or a note to see it here.</p>
        ) : (
          resumable.map((tab) => (
            <div className="dashboard__resume-row" key={tab.id}>
              <span className="dashboard__resume-bullet" />
              <span className="dashboard__resume-title">{tab.label}</span>
              <span className="dashboard__resume-context">{TAB_KIND_LABEL[tab.kind] ?? tab.kind}</span>
              <button type="button" className="dashboard__resume-link" onClick={() => onResume(tab.id)}>
                Resume →
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
