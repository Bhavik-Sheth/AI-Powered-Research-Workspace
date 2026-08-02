import { useEffect, useRef, useState } from "react";
import {
  createExperimentApiProjectsProjectIdExperimentsPost,
  listExperimentsApiProjectsProjectIdExperimentsGet,
  type Experiment,
} from "@research-os/api-client";

import type { ProjectSocket } from "../state/useProjectSocket";
import { ApprovalPrompt, type RunSpec } from "./ApprovalPrompt";
import "./ExperimentsBoard.css";

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

/**
 * A single notebook cell preview (MODULES.md Experiments Board: "hides … how
 * agent-written cells are marked unrun-and-pending"). `propose_cell` is a
 * backend call (Execution Sandbox, Phase 2.1) not built in this slice — this
 * component has no real cell data feeding it yet, but renders the required
 * visual treatment so Phase 2.2/2.3 can wire the approval flow against it
 * without redesigning the marking rule. A cell is only ever "unrun and
 * pending approval" or has output — there is no third, failed state (PRD §13
 * Phase 2, no "failed" anywhere in this system).
 *
 * `onRun` is the entry point into the consent gate (D31): when a caller has
 * a cell's code and container spec (still pending the notebook-fetch
 * endpoint, same gap noted above), passing `onRun` turns the pending badge
 * into the affordance that opens `ApprovalPrompt`. No spec/no `onRun` means
 * the badge stays inert, matching today's no-real-data state.
 */
export function CellPreview({
  code,
  hasOutput,
  onRun,
}: {
  code: string;
  hasOutput: boolean;
  onRun?: () => void;
}) {
  return (
    <div className="experiments__cell">
      <pre className="experiments__cell-code">{code}</pre>
      {!hasOutput &&
        (onRun ? (
          <button type="button" className="experiments__cell-badge experiments__cell-badge--button" onClick={onRun}>
            unrun — pending approval · Run
          </button>
        ) : (
          <span className="experiments__cell-badge">unrun — pending approval</span>
        ))}
    </div>
  );
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
  // The single open ApprovalPrompt for this board, if any (D31 entry point
  // from CellPreview's "Run" affordance). One dialog at a time — no global
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
  }, [socket]);

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
