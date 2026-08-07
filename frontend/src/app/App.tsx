import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getModelsApiSettingsModelsGet } from "@research-os/api-client";

import { OnboardingWizard } from "../onboarding/OnboardingWizard";
import { AppShell } from "./AppShell";
import { AppBootScreen } from "./ErrorBoundary";

async function fetchOnboardingState() {
  const { data } = await getModelsApiSettingsModelsGet({ throwOnError: true });
  return data;
}

/** Routes between the gated wizard and the main shell (D35: "the app returns to step 1"). */
export function App() {
  const queryClient = useQueryClient();
  const { data, isPending, isError, error, refetch } = useQuery({
    queryKey: ["settings", "models"],
    queryFn: fetchOnboardingState,
  });

  if (isPending) {
    return <AppBootScreen title="Loading…" />;
  }

  // A dead/unreachable backend is a distinct condition from "onboarding not
  // yet completed" — `data` is undefined either way, so this must branch on
  // `isError` explicitly rather than fall through to `!data?.onboarding_completed_at`,
  // which would otherwise silently show the onboarding wizard for a backend
  // that's simply not running.
  if (isError) {
    return (
      <AppBootScreen
        title="Could not reach the backend"
        message={error instanceof Error ? error.message : "Unknown error"}
        onRetry={() => refetch()}
      />
    );
  }

  if (!data.onboarding_completed_at) {
    return (
      <OnboardingWizard onComplete={() => queryClient.invalidateQueries({ queryKey: ["settings", "models"] })} />
    );
  }

  return <AppShell />;
}
