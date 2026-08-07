import type { PatchProjectPaperRequest, SaveProviderRequest } from "@research-os/api-client";

type Provider = SaveProviderRequest["provider"];

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
  openrouter: "OpenRouter",
  deepseek: "DeepSeek",
  custom: "Custom (OpenAI-compatible)",
  ollama: "Ollama (local)",
  vllm: "vLLM (local)",
};

export const LOCAL_PROVIDERS: ReadonlySet<Provider> = new Set(["ollama", "vllm"]);
