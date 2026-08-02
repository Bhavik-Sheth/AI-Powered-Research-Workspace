import { ProviderForm } from "../settings/ProviderForm";

/** Onboarding step 3 (D11/D13/D35) — at least one validated LLM endpoint. */
export function ProviderStep({ onNext }: { onNext: () => void }) {
  return <ProviderForm onSaved={onNext} />;
}
