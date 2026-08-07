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
}: {
  projectId: string;
  projectName: string;
  tabs: TabRef[];
  activeTabId: string | null;
  onResume: (tabId: string) => void;
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

      {error && summary && <ErrorCard title="Something went wrong" message={error} onRetry={refresh} />}

      <div className="dashboard__stats">
        <div className="dashboard__stat">
          <div className="dashboard__stat-label">Papers</div>
          <div className="dashboard__stat-value">{summary?.papers.total ?? "…"}</div>
          <div className="dashboard__stat-qualifier">{summary?.papers.qualifier ?? ""}</div>
        </div>
        <div className="dashboard__stat">
          <div className="dashboard__stat-label">Notes</div>
          <div className="dashboard__stat-value">{summary?.notes.total ?? "…"}</div>
          <div className="dashboard__stat-qualifier">{summary?.notes.qualifier ?? ""}</div>
        </div>
        <div className="dashboard__stat">
          <div className="dashboard__stat-label">Experiments</div>
          <div className="dashboard__stat-value">{summary?.experiments.total ?? "…"}</div>
          <div className="dashboard__stat-qualifier">{summary?.experiments.qualifier ?? ""}</div>
        </div>
        <div className="dashboard__stat">
          <div className="dashboard__stat-label">Feed</div>
          <div className="dashboard__stat-value">{summary?.feed.total ?? "…"}</div>
          <div className="dashboard__stat-qualifier">{summary?.feed.qualifier ?? ""}</div>
        </div>
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
