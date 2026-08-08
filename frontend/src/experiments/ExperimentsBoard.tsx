import { useEffect, useRef, useState } from "react";
import {
  createExperimentApiProjectsProjectIdExperimentsPost,
  getNotebookServerApiExperimentsExperimentIdNotebookServerGet,
  getRunSpecApiExperimentsExperimentIdRunSpecGet,
  listExperimentsApiProjectsProjectIdExperimentsGet,
  updateExperimentApiProjectsProjectIdExperimentsExperimentIdPatch,
  type Experiment,
} from "@research-os/api-client";

import { ErrorCard } from "../design/ErrorCard";
import { EXPERIMENT_STATUS_VALUES, experimentStatusLabel, type ExperimentStatus } from "../design/labels";
import { useCollapsible } from "../state/useCollapsible";
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

/** The Companion's `log_experiment`/`update_experiment` tools (D19) emit a
 * `ui_action` of the same name over this same shared socket — this board
 * already subscribes to it for run events, so reacting here (a `refresh()`
 * call, not a route transition) is one fewer prop threaded through
 * `AppShell`'s dispatcher, which exists for navigation, not this (Phase 6.3).
 */
function isExperimentLogAction(message: unknown): boolean {
  if (typeof message !== "object" || message === null || !("event" in message) || !("action" in message)) return false;
  const m = message as { event: unknown; action: unknown };
  return m.event === "ui_action" && (m.action === "log_experiment" || m.action === "update_experiment");
}

interface RunPanelState {
  experimentId: string;
  runId: string | null;
  lines: string[];
  status: "running" | "done" | "failed";
  exitCode: number | null;
}

const STATUS_COLUMNS: { status: ExperimentStatus; label: string }[] = [
  { status: "planned", label: "PLANNED" },
  { status: "remaining", label: "REMAINING" },
  { status: "in-progress", label: "IN PROGRESS" },
  { status: "done", label: "DONE" },
];

// Status badges (UI_DESIGN.md §4.6): done = filled accent, in-progress =
// outlined accent, planned/remaining = outlined neutral. Deliberately no
// entry ever maps to the danger family — there is no "failed" status
// (PRD §13 Phase 2 checklist). Display copy itself lives in
// `design/labels.ts`'s `experimentStatusLabel` (Rules.md: one place a wire
// value becomes display copy) — not re-declared here.

/** Rail-item triage dropdown (Phase 6.9) — done = filled accent tint,
 * in-progress = outlined accent, planned/remaining = outlined neutral
 * (same three-way treatment the static badge used pre-6.9, before both rail
 * item and detail header traded their read-only badges for these two
 * controls), applied to a `<select>` instead of a `<span>` so a rail item
 * can change status without being opened. */
function statusSelectClass(status: ExperimentStatus): string {
  if (status === "done") return "experiments__status-select experiments__status-select--done";
  if (status === "in-progress") return "experiments__status-select experiments__status-select--in-progress";
  return "experiments__status-select";
}

/** Detail-pane segmented control (Phase 6.9) — the currently-active segment
 * gets the accent treatment (filled for `done`, outlined otherwise);
 * inactive segments stay neutral. Never the danger family for any status
 * (PRD §13) — there is no branch here that reaches for it. */
function statusSegmentClass(status: ExperimentStatus, isActive: boolean): string {
  if (!isActive) return "experiments__segment";
  if (status === "done") return "experiments__segment experiments__segment--active experiments__segment--done";
  return "experiments__segment experiments__segment--active";
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
 * tracker (D17/D29): a ~240px rail of titles grouped under the four status
 * headings (Phase 6.8 — was a four-column kanban), plus a wide detail pane
 * hosting the selected experiment's live notebook. Status is the real
 * four-value enum; there is no "failed" status and the danger family is
 * never used here (PRD §13).
 *
 * `onNotebookWidthChange`, if given, is called with the detail pane's
 * measured width whenever an experiment is selected (and with `null` once
 * none is) — AppShell's own width-driven Companion auto-collapse (Phase 6.8)
 * reads this rather than duplicating a second ResizeObserver on the same
 * element from outside this module.
 */
export function ExperimentsBoard({
  projectId,
  socket,
  onNotebookWidthChange,
}: {
  projectId: string;
  socket: ProjectSocket;
  onNotebookWidthChange?: (width: number | null) => void;
}) {
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
  // The one selected rail item — shows its live notebook panel in the detail
  // pane. Only one at a time: opening a second live notebook server per
  // experiment isn't useful (each is its own long-lived container) and keeps
  // the lifecycle legible. (Was `expandedId`/kanban-column-expand pre-6.8;
  // same one-at-a-time invariant, just a rail selection now.)
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [measuredRunError, setMeasuredRunError] = useState<{ message: string; retry: () => void } | null>(null);
  // The one experiment whose status PATCH is in flight, if any (Phase 6.9) —
  // disables that item's own control only, not the whole board, while the
  // segmented control/rail dropdown's request is pending.
  const [statusUpdatingId, setStatusUpdatingId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [railCollapsed, toggleRail] = useCollapsible("experimentsRailCollapsed");
  const detailRef = useRef<HTMLDivElement | null>(null);

  function selectExperiment(experimentId: string) {
    setSelectedId((prev) => (prev === experimentId ? null : experimentId));
    setMeasuredRunError(null);
  }

  /** Phase 6.9: the only place this board writes `status` — the segmented
   * control in the detail pane header and each rail item's triage dropdown
   * both call this. PATCHes the existing
   * `PATCH /api/projects/:id/experiments/:experimentId` (already accepted
   * `status`; no backend change needed for this phase) and folds the
   * response straight into local `experiments` state — this board has no
   * React Query cache to invalidate (it owns its own `experiments` array via
   * `refresh()`), so updating that array directly is this component's own
   * existing convention for "the rail regroups immediately" rather than a
   * second fetch round-trip. */
  async function updateExperimentStatus(experimentId: string, status: ExperimentStatus) {
    setStatusUpdatingId(experimentId);
    try {
      const { data } = await updateExperimentApiProjectsProjectIdExperimentsExperimentIdPatch({
        path: { project_id: projectId, experiment_id: experimentId },
        body: { status },
        throwOnError: true,
      });
      setExperiments((prev) => prev.map((experiment) => (experiment.id === experimentId ? data : experiment)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update this experiment's status");
    } finally {
      setStatusUpdatingId(null);
    }
  }

  // Reports the detail pane's own measured width up to AppShell whenever it
  // (or the selection) changes — a plain ResizeObserver, not a shared hook
  // (`state/usePaneWidth.ts` already means something else here: a
  // user-dragged, persisted preference width for the nav/Companion panes,
  // reused as-is; this is a one-shot *measured* width with exactly one
  // consumer, so it stays local rather than forcing an unrelated concern
  // into that hook or its file-per-hook naming convention).
  useEffect(() => {
    const el = detailRef.current;
    if (!selectedId || !el) {
      onNotebookWidthChange?.(null);
      return;
    }
    const report = () => onNotebookWidthChange?.(el.clientWidth);
    report();
    const observer = new ResizeObserver(report);
    observer.observe(el);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

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
      if (isExperimentLogAction(message)) {
        void refresh();
        return;
      }
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

  const selectedExperiment = experiments.find((experiment) => experiment.id === selectedId) ?? null;

  return (
    <div className="experiments">
      {error && loaded && <ErrorCard title="Couldn't refresh" message={error} onRetry={refresh} />}
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

      <div className="experiments__layout">
        <div className={`experiments__rail ${railCollapsed ? "experiments__rail--collapsed" : ""}`}>
          <button
            type="button"
            className="experiments__rail-toggle"
            onClick={toggleRail}
            aria-label={railCollapsed ? "Expand experiment list" : "Collapse experiment list"}
          >
            {railCollapsed ? "›" : "‹"}
          </button>
          {!railCollapsed && (
            <div className="experiments__rail-body">
              {STATUS_COLUMNS.map(({ status, label }) => {
                const groupExperiments = experiments.filter((experiment) => experiment.status === status);
                return (
                  <div className="experiments__rail-group" key={status}>
                    <div className="experiments__rail-group-header">
                      <span>{label}</span>
                      <span className="experiments__column-count">{groupExperiments.length}</span>
                    </div>
                    {groupExperiments.length === 0 ? (
                      <p className="experiments__empty">Nothing here yet.</p>
                    ) : (
                      groupExperiments.map((experiment) => (
                        // A native `<select>` is interactive content, which
                        // HTML forbids nesting inside a `<button>` — this
                        // item is a `role="button"` div instead (Phase 6.9),
                        // same click/keyboard-activation contract as before,
                        // so the triage dropdown can sit beside it as a real
                        // sibling control rather than a nested one.
                        <div
                          key={experiment.id}
                          role="button"
                          tabIndex={0}
                          className={`experiments__rail-item ${selectedId === experiment.id ? "experiments__rail-item--active" : ""}`}
                          aria-current={selectedId === experiment.id}
                          onClick={() => selectExperiment(experiment.id)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              selectExperiment(experiment.id);
                            }
                          }}
                        >
                          <span className="experiments__rail-item-title">{experiment.title}</span>
                          <select
                            className={statusSelectClass(experiment.status)}
                            value={experiment.status}
                            disabled={statusUpdatingId === experiment.id}
                            aria-label={`Status for ${experiment.title}`}
                            // Stops the click that opens/changes this select
                            // from also bubbling up as a row-selection click
                            // on the rail item (D: triage without opening).
                            onClick={(event) => event.stopPropagation()}
                            onChange={(event) => {
                              event.stopPropagation();
                              void updateExperimentStatus(experiment.id, event.target.value as ExperimentStatus);
                            }}
                          >
                            {EXPERIMENT_STATUS_VALUES.map((status) => (
                              <option key={status} value={status}>
                                {experimentStatusLabel[status]}
                              </option>
                            ))}
                          </select>
                        </div>
                      ))
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="experiments__detail" ref={detailRef}>
          {!selectedExperiment ? (
            <p className="experiments__detail-empty">Select an experiment from the list to open its notebook.</p>
          ) : (
            <>
              <div className="experiments__detail-header">
                <h2 className="experiments__detail-title">{selectedExperiment.title}</h2>
                <div className="experiments__status-segmented" role="group" aria-label="Experiment status">
                  {EXPERIMENT_STATUS_VALUES.map((status) => (
                    <button
                      type="button"
                      key={status}
                      className={statusSegmentClass(status, selectedExperiment.status === status)}
                      aria-pressed={selectedExperiment.status === status}
                      disabled={statusUpdatingId === selectedExperiment.id}
                      onClick={() => void updateExperimentStatus(selectedExperiment.id, status)}
                    >
                      {experimentStatusLabel[status]}
                    </button>
                  ))}
                </div>
              </div>
              {selectedExperiment.hypothesis && (
                <p className="experiments__card-hypothesis">{selectedExperiment.hypothesis}</p>
              )}

              <div className="experiments__notebook">
                <LiveNotebookPanel experimentId={selectedExperiment.id} socket={socket} />
                <div className="experiments__measured-row">
                  <button
                    type="button"
                    className="experiments__measured-button"
                    onClick={() => void handleRecordMeasuredRun(selectedExperiment.id)}
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

              {runPanel && runPanel.experimentId === selectedExperiment.id && <RunLogPanel run={runPanel} />}

              {pendingApproval && pendingApproval.experimentId === selectedExperiment.id && (
                <ApprovalPrompt
                  experimentId={pendingApproval.experimentId}
                  code={pendingApproval.code}
                  spec={pendingApproval.spec}
                  onCancel={() => setPendingApproval(null)}
                  onApproved={() => {
                    // The dialog's job (consent) is done — swap it for the
                    // live run panel rather than just closing it (D31:
                    // approval always leads somewhere observable, never a
                    // silent dismissal).
                    setRunPanel({
                      experimentId: pendingApproval.experimentId,
                      runId: null,
                      lines: [],
                      status: "running",
                      exitCode: null,
                    });
                    setPendingApproval(null);
                  }}
                />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
