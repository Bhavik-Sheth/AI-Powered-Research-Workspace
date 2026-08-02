import { ErrorCard } from "../design/ErrorCard";
import { useReadiness } from "../state/useReadiness";

const DOCKER_INSTALL_COMMANDS = [
  "sudo dnf install docker-ce docker-ce-cli containerd.io docker-compose-plugin",
  "sudo systemctl enable --now docker",
  "sudo usermod -aG docker $USER   # then log out and back in",
].join("\n");

/** Onboarding step 1 (D35) — fails closed with the exact remediation commands. */
export function DockerCheckStep({ onNext }: { onNext: () => void }) {
  const { data, isPending, refetch } = useReadiness();
  const dockerState = data?.capabilities.docker;

  if (isPending) {
    return <p>Checking Docker…</p>;
  }

  if (dockerState !== "ready") {
    return (
      <ErrorCard
        title="Docker is not reachable"
        message={`Research Companion OS needs a running Docker daemon.\n\nInstall and start it, then restart this app:\n\n${DOCKER_INSTALL_COMMANDS}`}
        onRetry={() => refetch()}
      />
    );
  }

  return (
    <div className="wizard__actions">
      <p>Docker is reachable.</p>
      <button type="button" className="wizard__button" onClick={onNext}>
        Continue
      </button>
    </div>
  );
}
