import { useEffect, useState } from "react";
import {
  getDashboardApiProjectsProjectIdDashboardGet,
  reprocessPaperApiProjectsProjectIdPapersPaperIdReprocessPost,
  type DashboardSummary,
  type NeedsAttentionItem,
} from "@research-os/api-client";

import { ErrorCard } from "../design/ErrorCard";
import type { TabRef } from "../state/useTabStack";
import "./Dashboard.css";

// Segmented progress meter bands, in display order (Phase 6.10) — the same
// four `experiments.status` values, never a fifth "failed" band.
const PROGRESS_BANDS = ["planned", "remaining", "in_progress", "done"] as const;
const PROGRESS_BAND_LABEL: Record<(typeof PROGRESS_BANDS)[number], string> = {
  planned: "Planned",
  remaining: "Remaining",
  in_progress: "In progress",
  done: "Done",
};

const TAB_KIND_LABEL: Record<string, string> = {
  reader: "paper",
  library: "papers",
  notes: "notes",
  search: "search",
};

/** The project's landing view (UI_DESIGN.md §4.1) — real counts, the
 * persisted tab stack rendered as "continue where you left off" rows
 * (Phase 1.8 sign-off: resume position is real state, not a guess), and
 * `NEEDS ATTENTION` (Bug Fix Plan Phase 4.3): the same figures Library
 * View, Notes, Experiments Board and Feed View already show, projected
 * here so a failed or stalled paper is visible without opening the
 * library. Every number this screen renders comes from one REST read
 * (`GET /api/projects/:id/dashboard`) — Dashboard computes nothing itself. */
export function Dashboard({
  projectId,
  projectName,
  tabs,
  activeTabId,
  onResume,
  onOpenPaper,
}: {
  projectId: string;
  projectName: string;
  tabs: TabRef[];
  activeTabId: string | null;
  onResume: (tabId: string) => void;
  /** Opens a reader tab for a relevant-papers row (Phase 6.10) — the same
   * callback every other paper-listing view already receives. */
  onOpenPaper?: (paperId: string, title: string) => void;
}) {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setError(null);
    try {
      const { data } = await getDashboardApiProjectsProjectIdDashboardGet({
        path: { project_id: projectId },
        throwOnError: true,
      });
      setSummary(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the dashboard");
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

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

  const resumable = tabs.filter((t) => t.kind !== "dashboard");

  if (error && !summary) return <ErrorCard title="Could not load the dashboard" message={error} onRetry={refresh} />;

  return (
    <div className="dashboard">
      <h1 className="dashboard__title">{projectName}</h1>

      {error && summary && <ErrorCard title="Couldn't refresh" message={error} onRetry={refresh} />}

      <div className="dashboard__section">
        <h2 className="dashboard__section-label">Currently working on</h2>
        {summary && !summary.focus.focus_seed && summary.focus.in_progress_hypotheses.length === 0 ? (
          <p className="dashboard__empty">No focus set yet — add one from onboarding or the project settings.</p>
        ) : (
          <div className="dashboard__focus">
            {summary?.focus.focus_seed && <p className="dashboard__focus-seed">{summary.focus.focus_seed}</p>}
            {summary?.focus.in_progress_hypotheses.map((hypothesis, index) => (
              <p className="dashboard__focus-hypothesis" key={index}>
                {hypothesis}
              </p>
            ))}
          </div>
        )}
      </div>

      <div className="dashboard__section">
        <h2 className="dashboard__section-label">Progress</h2>
        {summary && (
          <>
            <div className="dashboard__progress-meter" role="img" aria-label="Experiment progress by status">
              {PROGRESS_BANDS.map((band) => {
                const total =
                  summary.progress.planned + summary.progress.remaining + summary.progress.in_progress + summary.progress.done;
                const count = summary.progress[band];
                const width = total > 0 ? (count / total) * 100 : 0;
                return count > 0 ? (
                  <div
                    key={band}
                    className={`dashboard__progress-band dashboard__progress-band--${band}`}
                    style={{ width: `${width}%` }}
                    title={`${PROGRESS_BAND_LABEL[band]}: ${count}`}
                  />
                ) : null;
              })}
            </div>
            <div className="dashboard__progress-legend">
              {PROGRESS_BANDS.map((band) => (
                <span className="dashboard__progress-legend-item" key={band}>
                  <span className={`dashboard__progress-swatch dashboard__progress-swatch--${band}`} />
                  {PROGRESS_BAND_LABEL[band]} ({summary.progress[band]})
                </span>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="dashboard__section">
        <h2 className="dashboard__section-label">Pending experiments</h2>
        {summary && summary.pending_experiments.length === 0 ? (
          <p className="dashboard__empty">Nothing pending — every experiment is in progress or done.</p>
        ) : (
          summary?.pending_experiments.map((experiment) => (
            <div className="dashboard__pending-row" key={experiment.id}>
              <span className="dashboard__pending-title">{experiment.title}</span>
              {experiment.hypothesis && <span className="dashboard__pending-hypothesis">{experiment.hypothesis}</span>}
            </div>
          ))
        )}
      </div>

      <div className="dashboard__section">
        <h2 className="dashboard__section-label">Relevant papers</h2>
        {summary && summary.relevant_papers.length === 0 ? (
          <p className="dashboard__empty">
            {summary.focus.focus_seed || summary.focus.in_progress_hypotheses.length > 0
              ? "No library papers ranked against the current focus yet."
              : "Set a focus to see your library ranked against it."}
          </p>
        ) : (
          summary?.relevant_papers.map((paper) => (
            <button
              type="button"
              className="dashboard__relevant-row"
              key={paper.paper_id}
              onClick={() => onOpenPaper?.(paper.paper_id, paper.title)}
            >
              {paper.title}
            </button>
          ))
        )}
      </div>

      <div className="dashboard__section">
        <h2 className="dashboard__section-label">Continue where you left off</h2>
        {resumable.length === 0 ? (
          <p className="dashboard__empty">Nothing open yet — open a paper or a note to see it here.</p>
        ) : (
          resumable.map((tab) => {
            const isActive = tab.id === activeTabId;
            return (
              <div
                className={`dashboard__resume-row${isActive ? " dashboard__resume-row--active" : ""}`}
                key={tab.id}
              >
                <span className="dashboard__resume-bullet" />
                <span className="dashboard__resume-title">{tab.label}</span>
                {isActive && <span className="dashboard__resume-badge">Current</span>}
                <span className="dashboard__resume-context">{TAB_KIND_LABEL[tab.kind] ?? tab.kind}</span>
                <button type="button" className="dashboard__resume-link" onClick={() => onResume(tab.id)}>
                  Resume →
                </button>
              </div>
            );
          })
        )}
      </div>

      <div className="dashboard__section">
        <h2 className="dashboard__section-label">Needs attention</h2>
        {summary && summary.needs_attention.length === 0 ? (
          <p className="dashboard__empty">Nothing needs attention right now.</p>
        ) : (
          summary?.needs_attention.map((item, index) => <NeedsAttentionRow key={index} item={item} onRetry={retry} />)
        )}
      </div>
    </div>
  );
}

function NeedsAttentionRow({ item, onRetry }: { item: NeedsAttentionItem; onRetry: (paperId: string) => void }) {
  if (item.severity === "error") {
    return (
      <ErrorCard
        title="Processing failed"
        message={item.message}
        onRetry={() => {
          if (item.paper_id) void onRetry(item.paper_id);
        }}
      />
    );
  }
  return (
    <div className="dashboard__nudge">
      <p className="dashboard__nudge-text">{item.message}</p>
      {item.action === "retry" && item.paper_id && (
        <button type="button" className="dashboard__nudge-retry" onClick={() => onRetry(item.paper_id!)}>
          Retry
        </button>
      )}
    </div>
  );
}
