"""Wire-shape models for Settings Store (Rules.md: Pydantic model names match the wire shape)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Provider = Literal["google", "groq", "openai", "anthropic", "openrouter", "deepseek", "custom", "ollama", "vllm"]

LOCAL_PROVIDERS: frozenset[str] = frozenset({"ollama", "vllm"})


class ProviderCredentials(BaseModel):
    """What the user submits to save and validate one provider."""

    api_key: str | None = None
    base_url: str | None = None
    model: str
    tier: Literal["primary", "auxiliary"] = "primary"
    request_token_budget: int | None = None
    """Per-request input-token ceiling for this provider's rate-limited tier
    (e.g. a free-tier TPM cap). NULL leaves requests unbounded — the caller's
    own provider limit is the only ceiling (Bug Fix Plan Phase 1.1)."""


class ProviderInfo(BaseModel):
    """What is ever read back — never ciphertext, nonce, or plaintext key."""

    last4: str | None = None
    base_url: str | None = None
    validated_at: datetime | None = None
    request_token_budget: int | None = None


class ModelSettings(BaseModel):
    providers: dict[str, ProviderInfo]
    primary_model: str | None = None
    auxiliary_model: str | None = None
    onboarding_completed_at: datetime | None = None
