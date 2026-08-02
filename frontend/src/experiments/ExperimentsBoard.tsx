import { useEffect, useState } from "react";
import {
  createExperimentApiProjectsProjectIdExperimentsPost,
  listExperimentsApiProjectsProjectIdExperimentsGet,
  type Experiment,
} from "@research-os/api-client";

import "./ExperimentsBoard.css";

type ExperimentStatus = Experiment["status"];

const STATUS_COLUMNS: { status: ExperimentStatus; label: string }[] = [
  { status: "planned", label: "PLANNED" },
  { status: "remaining", label: "REMAINING" },
  { status: "in-progress", label: "IN PROGRESS" },
  { status: "done", label: "DONE" },
];

// Status badges (UI_DESIGN.md §4.6): done = filled accent, in-progress =
// outlined accent, planned/remaining = outlined neutral. Deliberately no
// entry ever maps to the danger family — there is no "failed" status
// (PRD §13 Phase 2 checklist).
const STATUS_LABEL: Record<ExperimentStatus, string> = {
  planned: "Planned",
  remaining: "Remaining",
  "in-progress": "In progress",
  done: "Done",
};

function statusBadgeClass(status: ExperimentStatus): string {
  if (status === "done") return "experiments__badge experiments__badge--done";
  if (status === "in-progress") return "experiments__badge experiments__badge--in-progress";
  return "experiments__badge";
}

/**
 * A single notebook cell preview (MODULES.md Experiments Board: "hides … how
 * agent-written cells are marked unrun-and-pending"). `propose_cell` is a
 * backend call (Execution Sandbox, Phase 2.1) not built in this slice — this
 * component has no real cell data feeding it yet, but renders the required
 * visual treatment so Phase 2.2/2.3 can wire the approval flow against it
 * without redesigning the marking rule. A cell is only ever "unrun and
 * pending approval" or has output — there is no third, failed state (PRD §13
 * Phase 2, no "failed" anywhere in this system).
 */
export function CellPreview({ code, hasOutput }: { code: string; hasOutput: boolean }) {
  return (
    <div className="experiments__cell">
      <pre className="experiments__cell-code">{code}</pre>
      {!hasOutput && <span className="experiments__cell-badge">unrun — pending approval</span>}
    </div>
  );
}

/** Experiments Board (MODULES.md) — the project's lab notebook, not a run
 * tracker (D17/D29): four status columns, hypothesis-first cards, and a
 * minimal create form. Status is the real four-value enum; there is no
 * "failed" status and the danger family is never used here (PRD §13). */
export function ExperimentsBoard({ projectId }: { projectId: string }) {
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [loading, setLoading] = useState(true);
  const [newTitle, setNewTitle] = useState("");
  const [creating, setCreating] = useState(false);

  async function refresh() {
    const { data } = await listExperimentsApiProjectsProjectIdExperimentsGet({
      path: { project_id: projectId },
      throwOnError: true,
    });
    setExperiments(data);
    setLoading(false);
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function handleCreate() {
    if (!newTitle.trim()) return;
    setCreating(true);
    try {
      await createExperimentApiProjectsProjectIdExperimentsPost({
        path: { project_id: projectId },
        body: { title: newTitle },
        throwOnError: true,
      });
      setNewTitle("");
      await refresh();
    } finally {
      setCreating(false);
    }
  }

  if (loading) return <p>Loading…</p>;

  return (
    <div className="experiments">
      <div className="experiments__header">
        <div>
          <h1 className="experiments__title">Experiments</h1>
          <span className="experiments__subtitle">lab notebook</span>
        </div>
        <form
          className="experiments__new"
          onSubmit={(event) => {
            event.preventDefault();
            void handleCreate();
          }}
        >
          <input
            className="experiments__new-input"
            value={newTitle}
            onChange={(event) => setNewTitle(event.target.value)}
            placeholder="New experiment title"
          />
          <button type="submit" className="experiments__new-button" disabled={creating || !newTitle.trim()}>
            + New experiment
          </button>
        </form>
      </div>

      <div className="experiments__board">
        {STATUS_COLUMNS.map(({ status, label }) => {
          const columnExperiments = experiments.filter((experiment) => experiment.status === status);
          return (
            <div className="experiments__column" key={status}>
              <div className="experiments__column-header">
                <span>{label}</span>
                <span className="experiments__column-count">{columnExperiments.length}</span>
              </div>
              <div className="experiments__column-body">
                {columnExperiments.length === 0 ? (
                  <p className="experiments__empty">Nothing here yet.</p>
                ) : (
                  columnExperiments.map((experiment) => (
                    <div className="experiments__card" key={experiment.id}>
                      <div className="experiments__card-head">
                        <span className="experiments__card-title">{experiment.title}</span>
                        <span className={statusBadgeClass(experiment.status)}>
                          {STATUS_LABEL[experiment.status]}
                        </span>
                      </div>
                      {experiment.hypothesis && (
                        <p className="experiments__card-hypothesis">{experiment.hypothesis}</p>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
