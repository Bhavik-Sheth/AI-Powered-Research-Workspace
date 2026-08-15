import type { Experiment, PatchProjectPaperRequest, PutVoiceSettingsRequest, SaveProviderRequest } from "@research-os/api-client";

type Provider = SaveProviderRequest["provider"];

/** The four-value experiment status enum (`experiments.status`,
 * `experiments_status_check`) — reuses the generated client's own
 * `Experiment["status"]` rather than hand-declaring a second copy of it
 * (Rules.md: never hand-declare a type the generated client already
 * exports). */
export type ExperimentStatus = Experiment["status"];

export const EXPERIMENT_STATUS_VALUES: readonly ExperimentStatus[] = [
  "planned",
  "remaining",
  "in-progress",
  "done",
];

/** The one place a wire enum value becomes display copy (Rules.md) — used
 * by Experiments Board's rail badges, detail-pane segmented control, and
 * rail-item status dropdown (Phase 6.9). No `failed` value and never the
 * danger family for a status (PRD §13) — this map only ever holds the four
 * real values. */
export const experimentStatusLabel: Record<ExperimentStatus, string> = {
  planned: "Planned",
  remaining: "Remaining",
  "in-progress": "In progress",
  done: "Done",
};

/** The four-value relevance enum (`project_papers.relevance`, D22/D25) —
 * reuses the generated client's own union (`PatchProjectPaperRequest`)
 * rather than hand-declaring a second copy of it (Rules.md: never
 * hand-declare a type the generated client already exports). */
export type Relevance = NonNullable<PatchProjectPaperRequest["relevance"]>;

export const RELEVANCE_VALUES: readonly Relevance[] = ["relevant", "somewhat", "not", "unset"];

/** The one place a wire enum value becomes display copy (Rules.md) — three
 * presentations share it (UI_DESIGN.md §3.2): the Reader header's
 * segmented control, and Library's relevance badges + filter-chip row.
 * `not` → "not relevant" and `unset` → "unmarked" everywhere, per §3.2's
 * own copy rule. */
export const relevanceLabel: Record<Relevance, string> = {
  relevant: "relevant",
  somewhat: "somewhat",
  not: "not relevant",
  unset: "unmarked",
};

/** The one place a wire enum value becomes display copy (Rules.md). */
export const providerLabel: Record<Provider, string> = {
  google: "Google (Gemini)",
  groq: "Groq",
  openai: "OpenAI",
  anthropic: "Anthropic",
  mistral: "Mistral",
  openrouter: "OpenRouter",
  deepseek: "DeepSeek",
  custom: "Custom (OpenAI-compatible)",
  ollama: "Ollama (local)",
  vllm: "vLLM (local)",
};

export const LOCAL_PROVIDERS: ReadonlySet<Provider> = new Set(["ollama", "vllm"]);

/** The two selectable voice engine profiles (Voice Layer Plan V10/V11) —
 * reuses the generated client's own union (`PutVoiceSettingsRequest`)
 * rather than hand-declaring a second copy of it (Rules.md). `whisper_cpp`
 * is a real CHECK value with no registered engine (V11) and is deliberately
 * absent here — the selector only ever offers a profile that actually
 * works. */
export type VoiceEngine = NonNullable<PutVoiceSettingsRequest["voice_engine"]>;

export const VOICE_ENGINE_VALUES: readonly VoiceEngine[] = ["faster_whisper", "stub"];

/** The one place a wire enum value becomes display copy (Rules.md). */
export const voiceEngineLabel: Record<VoiceEngine, string> = {
  faster_whisper: "Real voice (faster-whisper + Piper)",
  stub: "Stub (no real transcription or speech)",
};
