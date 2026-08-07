import { useEffect, useRef, useState } from "react";
import {
  createConfirmationApiExperimentsExperimentIdConfirmationPost,
  runAllApiExperimentsExperimentIdRunAllPost,
  type RunSpec,
} from "@research-os/api-client";

import "./ApprovalPrompt.css";

export type { RunSpec };

/**
 * The consent gate (D31): the only UI surface that can produce a
 * confirmation token, and therefore the only path to `run_all`. Shows the
 * code and the full container spec side by side — never a JSON dump — per
 * PRD §13 Phase 2 checklist. There is no default-confirmed state and no
 * enter-to-confirm shortcut: approval is a deliberate, single explicit
 * action (invariant #5), not a UX smoothing target.
 *
 * Rendered as a native `<dialog>` opened with `.showModal()` (Phase 4.2) —
 * a Web-Platform builtin gives this the real focus trap and Escape-to-close
 * its `aria-modal` claim always implied, for free, ahead of a hand-rolled
 * Tab-cycling trap (Rules.md resolution order). `ExperimentsBoard` already
 * mounts this component only while an approval is pending, so "open" is
 * just "mounted" — no separate open/closed prop to key an effect on.
 */
export function ApprovalPrompt({
  experimentId,
  code,
  spec,
  onApproved,
  onCancel,
}: {
  experimentId: string;
  code: string;
  spec: RunSpec;
  onApproved: () => void;
  onCancel: () => void;
}) {
  const [approving, setApproving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isNetworked = spec.network === "bridge";
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<Element | null>(null);

  // Open modally on mount, and return focus to whatever element opened this
  // (the "Record Measured Run" button) once it unmounts — approved or
  // cancelled, this component is unmounted either way (ExperimentsBoard
  // clears `pendingApproval`), so one effect covers both paths.
  useEffect(() => {
    triggerRef.current = document.activeElement;
    dialogRef.current?.showModal();
    return () => {
      if (triggerRef.current instanceof HTMLElement) triggerRef.current.focus();
    };
  }, []);

  // Escape fires the dialog's native "cancel" event first. Route it through
  // the exact same deny path the Cancel button uses instead of letting the
  // browser's default action close the element out from under React's
  // `pendingApproval` state — invariant #5 requires Escape to be a real
  // deny, never a silent no-op leaving an ambiguous "maybe approved" state.
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    function handleCancel(event: Event) {
      event.preventDefault();
      onCancel();
    }
    dialog.addEventListener("cancel", handleCancel);
    return () => dialog.removeEventListener("cancel", handleCancel);
  }, [onCancel]);

  async function handleApprove() {
    setApproving(true);
    setError(null);
    try {
      const { data: token } = await createConfirmationApiExperimentsExperimentIdConfirmationPost({
        path: { experiment_id: experimentId },
        throwOnError: true,
      });
      await runAllApiExperimentsExperimentIdRunAllPost({
        path: { experiment_id: experimentId },
        body: { confirmation_token: token.token, network_optin: isNetworked, gpu: spec.gpu },
        throwOnError: true,
      });
      onApproved();
    } catch {
      setError("Could not start the run. Try again.");
    } finally {
      setApproving(false);
    }
  }

  return (
    <dialog ref={dialogRef} className="approval-prompt" aria-label="Approve run">
      <div className="approval-prompt__header">
        <h2 className="approval-prompt__title">Approve &amp; run</h2>
        <span className="approval-prompt__subtitle">
          This code will execute inside an isolated Docker container. Nothing runs until you confirm.
        </span>
      </div>

      <section className="approval-prompt__section">
        <h3 className="approval-prompt__section-title">Code</h3>
        <pre className="approval-prompt__code">{code}</pre>
      </section>

      <section className="approval-prompt__section">
        <h3 className="approval-prompt__section-title">Container spec</h3>
        <dl className="approval-prompt__grid">
          <dt>Image</dt>
          <dd>{spec.image}</dd>

          <dt>Network</dt>
          <dd>
            <span className={isNetworked ? "approval-prompt__network approval-prompt__network--bridge" : ""}>
              {spec.network}
            </span>
            {isNetworked && (
              <span className="approval-prompt__flag">
                networked run — opt-in, less reproducible
              </span>
            )}
          </dd>

          <dt>CPU limit</dt>
          <dd>{spec.cpu_limit} cores</dd>

          <dt>Memory limit</dt>
          <dd>{spec.memory_limit_mb} MB</dd>

          <dt>Idle timeout</dt>
          <dd>{spec.idle_timeout_seconds}s</dd>

          <dt>Per-cell timeout</dt>
          <dd>{spec.cell_timeout_seconds}s</dd>

          <dt>GPU</dt>
          <dd>{spec.gpu ? "enabled" : "off"}</dd>
        </dl>

        <h4 className="approval-prompt__mounts-title">Mounts</h4>
        {spec.mounts.length === 0 ? (
          <p className="approval-prompt__empty">No mounts.</p>
        ) : (
          <ul className="approval-prompt__mounts">
            {spec.mounts.map((mount, index) => (
              <li className="approval-prompt__mount" key={`${mount.host_path}-${index}`}>
                <code>{mount.host_path}</code>
                <span className="approval-prompt__mount-arrow">→</span>
                <code>{mount.container_path}</code>
                <span className="approval-prompt__mount-mode">{mount.mode}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {error && <p className="approval-prompt__error">{error}</p>}

      <div className="approval-prompt__actions">
        <button
          type="button"
          className="approval-prompt__button approval-prompt__button--cancel"
          onClick={onCancel}
          disabled={approving}
        >
          Cancel
        </button>
        <button
          type="button"
          className="approval-prompt__button approval-prompt__button--approve"
          onClick={() => void handleApprove()}
          disabled={approving}
        >
          {approving ? "Starting…" : "Approve & Run"}
        </button>
      </div>
    </dialog>
  );
}
