import type { SaveProviderRequest } from "@research-os/api-client";

type Provider = SaveProviderRequest["provider"];

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
