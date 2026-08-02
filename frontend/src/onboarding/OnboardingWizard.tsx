import { useState } from "react";

import { DockerCheckStep } from "./DockerCheckStep";
import { ProjectStep } from "./ProjectStep";
import { ProviderStep } from "./ProviderStep";
import { VaultStep } from "./VaultStep";
import "./wizard.css";

const STEP_TITLES = ["Check Docker", "Choose a vault folder", "Connect a model", "Create your first project"];

/**
 * The gated four-step setup wizard (D35, MODULES.md Onboarding Wizard).
 * All four steps are required, in order; it cannot be skipped or reordered.
 */
export function OnboardingWizard({ onComplete }: { onComplete: () => void }) {
  const [step, setStep] = useState(0);

  const steps = [
    <DockerCheckStep key="docker" onNext={() => setStep(1)} />,
    <VaultStep key="vault" onNext={() => setStep(2)} />,
    <ProviderStep key="provider" onNext={() => setStep(3)} />,
    <ProjectStep key="project" onDone={onComplete} />,
  ];

  return (
    <div className="wizard">
      <div className="wizard__steps">
        {STEP_TITLES.map((title, index) => (
          <div
            key={title}
            className={`wizard__step-dot ${index < step ? "wizard__step-dot--done" : index === step ? "wizard__step-dot--active" : ""}`}
          />
        ))}
      </div>
      <h1 className="wizard__title">{STEP_TITLES[step]}</h1>
      <p className="wizard__subtitle">
        Step {step + 1} of {STEP_TITLES.length}
      </p>
      {steps[step]}
    </div>
  );
}
