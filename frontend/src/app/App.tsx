import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getModelsApiSettingsModelsGet } from "@research-os/api-client";

import { OnboardingWizard } from "../onboarding/OnboardingWizard";
import { AppShell } from "./AppShell";

async function fetchOnboardingState() {
  const { data } = await getModelsApiSettingsModelsGet({ throwOnError: true });
  return data;
}

/** Routes between the gated wizard and the main shell (D35: "the app returns to step 1"). */
export function App() {
  const queryClient = useQueryClient();
  const { data, isPending } = useQuery({ queryKey: ["settings", "models"], queryFn: fetchOnboardingState });

  if (isPending) {
    return null;
  }

  if (!data?.onboarding_completed_at) {
    return (
      <OnboardingWizard onComplete={() => queryClient.invalidateQueries({ queryKey: ["settings", "models"] })} />
    );
  }

  return <AppShell />;
}
