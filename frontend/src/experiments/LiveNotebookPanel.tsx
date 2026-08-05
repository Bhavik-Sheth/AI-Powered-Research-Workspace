import { useEffect, useState } from "react";
import {
  getNotebookServerApiExperimentsExperimentIdNotebookServerGet,
  notebookServerActionApiExperimentsExperimentIdNotebookServerPost,
  type NotebookServerStatus,
} from "@research-os/api-client";

import type { ProjectSocket } from "../state/useProjectSocket";
import "./LiveNotebookPanel.css";

interface NotebookServerStoppedEvent {
  event: "notebook_server_stopped";
  experiment_id: string;
  reason: "manual" | "ceiling";
}

function isNotebookServerStoppedEvent(message: unknown): message is NotebookServerStoppedEvent {
  return (
    typeof message === "object" &&
    message !== null &&
    (message as { event?: unknown }).event === "notebook_server_stopped"
  );
}

/**
 * A real, live, interactive Jupyter notebook per experiment (Phase 2.4) —
 * embedded via `<iframe>`, not a custom editor. `backend/sandbox`'s
 * `start_notebook_server` runs the actual Jupyter server inside a
 * long-lived per-experiment container; this panel owns exactly the
 * start-on-mount / stop-on-unmount lifecycle plus the explicit "Stop
 * notebook" affordance — everything else (cell editing, running cells in
 * any order, rich outputs, markdown) is Jupyter's own UI, running inside
 * the iframe's own browsing context.
 */
export function LiveNotebookPanel({
  experimentId,
  socket,
}: {
  experimentId: string;
  socket: ProjectSocket;
}) {
  const [status, setStatus] = useState<NotebookServerStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [closedReason, setClosedReason] = useState<"manual" | "ceiling" | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function start() {
      setError(null);
      setClosedReason(null);
      try {
        const { data } = await notebookServerActionApiExperimentsExperimentIdNotebookServerPost({
          path: { experiment_id: experimentId },
          body: { action: "start" },
          throwOnError: true,
        });
        if (!cancelled) setStatus(data);
      } catch {
        if (!cancelled) setError("Could not start the live notebook. Try again.");
      }
    }

    void start();
    return () => {
      cancelled = true;
      void notebookServerActionApiExperimentsExperimentIdNotebookServerPost({
        path: { experiment_id: experimentId },
        body: { action: "stop" },
        throwOnError: true,
      });
    };
  }, [experimentId]);

  useEffect(() => {
    return socket.subscribe((message) => {
      if (!isNotebookServerStoppedEvent(message)) return;
      if (message.experiment_id !== experimentId) return;
      setClosedReason(message.reason);
      setStatus(null);
    });
  }, [socket, experimentId]);

  async function handleStopClick() {
    await notebookServerActionApiExperimentsExperimentIdNotebookServerPost({
      path: { experiment_id: experimentId },
      body: { action: "stop" },
      throwOnError: true,
    });
    setStatus(null);
    setClosedReason("manual");
  }

  async function handleReopen() {
    setClosedReason(null);
    const { data } = await getNotebookServerApiExperimentsExperimentIdNotebookServerGet({
      path: { experiment_id: experimentId },
      throwOnError: true,
    });
    if (data.state === "stopped") {
      const { data: started } = await notebookServerActionApiExperimentsExperimentIdNotebookServerPost({
        path: { experiment_id: experimentId },
        body: { action: "start" },
        throwOnError: true,
      });
      setStatus(started);
    } else {
      setStatus(data);
    }
  }

  if (error) {
    return <p className="live-notebook__error">{error}</p>;
  }

  if (closedReason === "ceiling") {
    return (
      <div className="live-notebook__closed">
        <p>Your notebook session was closed after being open a long time.</p>
        <button type="button" onClick={() => void handleReopen()}>
          Reopen
        </button>
      </div>
    );
  }

  if (status === null || status.state !== "running" || !status.url) {
    return <p className="live-notebook__loading">Starting notebook…</p>;
  }

  return (
    <div className="live-notebook">
      <div className="live-notebook__toolbar">
        <span className="live-notebook__status">● live</span>
        <button type="button" className="live-notebook__stop" onClick={() => void handleStopClick()}>
          Stop notebook
        </button>
      </div>
      {/* JupyterLab's own UI (running inside the iframe) hard-codes a
          760px "mobile layout" breakpoint below which its toolbar, kernel
          picker and cell gutter start overlapping — that floor isn't ours
          to change, so this wrapper scrolls horizontally on its own when
          the pane is narrower, instead of the framed app squeezing into a
          broken layout or the whole board scrolling. */}
      <div className="live-notebook__iframe-frame">
        <iframe
          src={status.url}
          className="live-notebook__iframe"
          title="Jupyter notebook"
          // A per-container, per-launch ephemeral loopback server (D2) — the
          // real security boundary here, not sandboxing this iframe further.
        />
      </div>
    </div>
  );
}
