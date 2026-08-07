import { useEffect, useRef, useState } from "react";
import {
  createExperimentApiProjectsProjectIdExperimentsPost,
  getNotebookServerApiExperimentsExperimentIdNotebookServerGet,
  getRunSpecApiExperimentsExperimentIdRunSpecGet,
  listExperimentsApiProjectsProjectIdExperimentsGet,
  type Experiment,
} from "@research-os/api-client";

import { ErrorCard } from "../design/ErrorCard";
import type { ProjectSocket } from "../state/useProjectSocket";
import { ApprovalPrompt, type RunSpec } from "./ApprovalPrompt";
import "./ExperimentsBoard.css";
import { LiveNotebookPanel } from "./LiveNotebookPanel";

// Wire shapes for the two run events (backend/sandbox/models.py
// `RunLogEvent`/`RunStatusEvent`) broadcast over the same per-project socket
// Companion uses. Declared here, not in `companion/wsTypes.ts`, because a
// run is not a Companion turn (see `backend/ws/__init__.py`'s module
// docstring) — this is the one place that needs to know these shapes.
interface RunLogEvent {
  event: "run_log";
  experiment_id: string;
  run_id: string;
  line: string;
}

interface RunStatusEvent {
  event: "run_status";
  experiment_id: string;
  run_id: string;
  status: "running" | "done" | "failed";
  exit_code: number | null;
}

function isRunEvent(message: unknown): message is RunLogEvent | RunStatusEvent {
  if (typeof message !== "object" || message === null || !("event" in message)) return false;
  const kind = (message as { event: unknown }).event;
  return kind === "run_log" || kind === "run_status";
}

interface RunPanelState {
  experimentId: string;
  runId: string | null;
  lines: string[];
  status: "running" | "done" | "failed";
  exitCode: number | null;
}

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

// A run's own lifecycle ("running" → "done"/"failed") is not the
// experiment's board status (PRD §13 forbids a "failed" *status* and the
// danger family for status, full stop) — it's a separate, transient fact
// about one approved run. `failed` here still never touches the danger
// tokens (`--danger-*`, reserved for actual errors, tokens.css); the ⚠
// treatment matches the non-danger "unverified" precedent in
// `companion/parseCitations.tsx` — dashed border, muted ink, a glyph, no red.
function runStatusLabel(status: RunPanelState["status"], exitCode: number | null): string {
  if (status === "running") return "● running";
  if (status === "failed") return `⚠ exited ${exitCode ?? "?"}`;
  return "done";
}

function runStatusClass(status: RunPanelState["status"]): string {
  if (status === "running") return "experiments__run-status experiments__run-status--running";
  if (status === "failed") return "experiments__run-status experiments__run-status--failed";
  return "experiments__run-status experiments__run-status--done";
}

/** Replaces `ApprovalPrompt` once a run is approved (D31's job there is
 * done) — live `run_log` lines streamed over the shared project socket,
 * appended as they arrive, plus the run's status transition. */
function RunLogPanel({ run }: { run: RunPanelState }) {
  return (
    <div className="experiments__run-panel" role="status" aria-label="Run in progress">
      <div className="experiments__run-panel-header">
        <span className="experiments__run-panel-title">Run</span>
        <span className={runStatusClass(run.status)}>{runStatusLabel(run.status, run.exitCode)}</span>
      </div>
      <pre className="experiments__run-log">
        {run.lines.length === 0 ? "Waiting for output…" : run.lines.join("\n")}
      </pre>
    </div>
  );
}

/** Experiments Board (MODULES.md) — the project's lab notebook, not a run
 * tracker (D17/D29): four status columns, hypothesis-first cards, and a
 * minimal create form. Status is the real four-value enum; there is no
 * "failed" status and the danger family is never used here (PRD §13). */
export function ExperimentsBoard({ projectId, socket }: { projectId: string; socket: ProjectSocket }) {
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [loading, setLoading] = useState(true);
  const [newTitle, setNewTitle] = useState("");
  const [creating, setCreating] = useState(false);
  // The single open ApprovalPrompt for this board, if any (D31 entry point:
  // the "Record Measured Run" button). One dialog at a time — no global
  // modal-management system for what is, by design, a single-flight gate.
  const [pendingApproval, setPendingApproval] = useState<{
    experimentId: string;
    code: string;
    spec: RunSpec;
  } | null>(null);
  // The one run currently being watched, scoped to whichever experiment was
  // just approved — mirrors `pendingApproval`'s single-flight shape rather
  // than a `Map<experimentId, ...>`: this board only ever has one approval
  // gate open at a time, so it only ever has one run to watch live.
  const [runPanel, setRunPanel] = useState<RunPanelState | null>(null);
  const runPanelExperimentIdRef = useRef<string | null>(null);
  runPanelExperimentIdRef.current = runPanel?.experimentId ?? null;
  // The one expanded card — shows its live notebook panel. Only one at a
  // time: opening a second live notebook server per card isn't useful (each
  // is its own long-lived container) and keeps the lifecycle legible.
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [measuredRunError, setMeasuredRunError] = useState<{ message: string; retry: () => void } | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggleExpand(experimentId: string) {
    setExpandedId((prev) => (prev === experimentId ? null : experimentId));
    setMeasuredRunError(null);
  }

  /** Entry point into the *unchanged* measured-run pipeline (`ApprovalPrompt`
   * → confirm → `run_all`). Deliberately not called "Restart & Run All" —
   * Jupyter's own iframe has its own native "Kernel → Restart & Run All"
   * menu item that does something else entirely (an interactive re-run,
   * producing no provenance record) and the two must not be confused. Checks
   * the live-notebook-server status first so a live notebook being open
   * surfaces a specific message here rather than `ApprovalPrompt`'s generic
   * failure text (that component stays unchanged). */
  async function handleRecordMeasuredRun(experimentId: string) {
    setMeasuredRunError(null);
    try {
      const { data: serverStatus } = await getNotebookServerApiExperimentsExperimentIdNotebookServerGet({
        path: { experiment_id: experimentId },
        throwOnError: true,
      });
      if (serverStatus.state !== "stopped") {
        setMeasuredRunError({
          message: "Stop the live notebook before running a measured pass.",
          retry: () => void handleRecordMeasuredRun(experimentId),
        });
        return;
      }
      const { data } = await getRunSpecApiExperimentsExperimentIdRunSpecGet({
        path: { experiment_id: experimentId },
        throwOnError: true,
      });
      const code =
        data.notebook.cells
          .filter((cell) => cell.cell_type === "code")
          .map((cell) => cell.source)
          .join("\n\n# ---\n\n") || "(empty notebook)";
      setPendingApproval({ experimentId, code, spec: data.run_spec });
    } catch (err) {
      setMeasuredRunError({
        message: err instanceof Error ? err.message : "Could not prepare this run",
        retry: () => void handleRecordMeasuredRun(experimentId),
      });
    }
  }

  useEffect(() => {
    return socket.subscribe((message) => {
      if (!isRunEvent(message)) return; // not ours — e.g. a Companion turn event
      if (message.experiment_id !== runPanelExperimentIdRef.current) return;
      if (message.event === "run_log") {
        setRunPanel((prev) => (prev ? { ...prev, runId: message.run_id, lines: [...prev.lines, message.line] } : prev));
      } else {
        setRunPanel((prev) =>
          prev ? { ...prev, runId: message.run_id, status: message.status, exitCode: message.exit_code } : prev,
        );
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [socket]);

  async function refresh() {
    try {
      const { data } = await listExperimentsApiProjectsProjectIdExperimentsGet({
        path: { project_id: projectId },
        throwOnError: true,
      });
      setExperiments(data);
      setLoaded(true);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load experiments");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function handleCreate() {
    if (!newTitle.trim()) return;
    setCreating(true);
    setError(null);
    try {
      await createExperimentApiProjectsProjectIdExperimentsPost({
        path: { project_id: projectId },
        body: { title: newTitle },
        throwOnError: true,
      });
      setNewTitle("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create this experiment");
    } finally {
      setCreating(false);
    }
  }

  if (loading) return <p>Loading…</p>;
  if (error && !loaded) return <ErrorCard title="Could not load experiments" message={error} onRetry={refresh} />;

  return (
    <div className="experiments">
      {error && loaded && <ErrorCard title="Something went wrong" message={error} onRetry={refresh} />}
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
                      <div
                        className="experiments__card-head"
                        style={{ cursor: "pointer" }}
                        onClick={() => void toggleExpand(experiment.id)}
                      >
                        <span className="experiments__card-title">{experiment.title}</span>
                        <span className={statusBadgeClass(experiment.status)}>
                          {STATUS_LABEL[experiment.status]}
                        </span>
                      </div>
                      {experiment.hypothesis && (
                        <p className="experiments__card-hypothesis">{experiment.hypothesis}</p>
                      )}

                      {expandedId === experiment.id && (
                        <div className="experiments__notebook">
                          <LiveNotebookPanel experimentId={experiment.id} socket={socket} />
                          <div className="experiments__measured-row">
                            <button
                              type="button"
                              className="experiments__measured-button"
                              onClick={() => void handleRecordMeasuredRun(experiment.id)}
                            >
                              Record Measured Run
                            </button>
                            <span className="experiments__measured-hint">
                              runs the whole notebook fresh, in an isolated container, for a citable result
                            </span>
                          </div>
                          {measuredRunError && (
                            <ErrorCard
                              title="Could not start the measured run"
                              message={measuredRunError.message}
                              onRetry={measuredRunError.retry}
                            />
                          )}
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          );
        })}
      </div>

      {pendingApproval && (
        <ApprovalPrompt
          experimentId={pendingApproval.experimentId}
          code={pendingApproval.code}
          spec={pendingApproval.spec}
          onCancel={() => setPendingApproval(null)}
          onApproved={() => {
            // The dialog's job (consent) is done — swap it for the live
            // run panel rather than just closing it (D31: approval always
            // leads somewhere observable, never a silent dismissal).
            setRunPanel({ experimentId: pendingApproval.experimentId, runId: null, lines: [], status: "running", exitCode: null });
            setPendingApproval(null);
          }}
        />
      )}

      {runPanel && <RunLogPanel run={runPanel} />}
    </div>
  );
}
