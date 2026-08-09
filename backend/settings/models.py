"""Wire-shape models for Settings Store (Rules.md: Pydantic model names match the wire shape)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Provider = Literal["google", "groq", "openai", "anthropic", "mistral", "openrouter", "deepseek", "custom", "ollama", "vllm"]

LOCAL_PROVIDERS: frozenset[str] = frozenset({"ollama", "vllm"})


class ProviderCredentials(BaseModel):
    """What the user submits to save and validate one provider."""

    api_key: str | None = None
    base_url: str | None = None
    model: str
    tier: Literal["primary", "auxiliary"] = "primary"
    model_budgets: dict[str, int] = Field(default_factory=dict)
    """Per-request input-token ceiling per model hosted by this provider,
    keyed by the full stored model string (e.g. `"groq/llama-3.1-8b-instant"`
    — exactly the form `primary_model`/`auxiliary_model` store). One provider
    can host models with different TPM ceilings, so a single flat budget
    cannot serve all of them. A model absent from this map is unbounded —
    the caller's own provider limit is the only ceiling — until LLM
    Gateway's self-heal path (Phase 2.1) learns it from a 429 body."""


class ProviderInfo(BaseModel):
    """What is ever read back — never ciphertext, nonce, or plaintext key."""

    last4: str | None = None
    base_url: str | None = None
    validated_at: datetime | None = None
    model_budgets: dict[str, int] = Field(default_factory=dict)


class ModelSettings(BaseModel):
    providers: dict[str, ProviderInfo]
    primary_model: str | None = None
    auxiliary_model: str | None = None
    onboarding_completed_at: datetime | None = None
