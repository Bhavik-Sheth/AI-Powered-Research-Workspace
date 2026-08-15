"""`GET/PUT /api/settings/models` and `GET/PUT /api/settings/voice` — back
Settings Store (TRD §4.2, Voice Layer Plan V10/V12)."""

from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
import settings
import voice
from settings.models import ModelSettings, Provider, ProviderCredentials

router = APIRouter()


@router.get("/api/settings/models", response_model=ModelSettings)
async def get_models() -> ModelSettings:
    async with db.session() as session:
        return await settings.get_settings(session)


class SaveProviderRequest(BaseModel):
    provider: Provider
    credentials: ProviderCredentials


@router.put("/api/settings/models", response_model=ModelSettings)
async def put_models(body: SaveProviderRequest) -> ModelSettings:
    async with db.session() as session:
        try:
            return await settings.save_provider(session, body.provider, body.credentials)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


class SaveModelBudgetRequest(BaseModel):
    provider: Provider
    model: str
    budget: int


@router.put("/api/settings/models/budget", response_model=ModelSettings)
async def put_model_budget(body: SaveModelBudgetRequest) -> ModelSettings:
    """Hand-set override for `model_budgets[f"{provider}/{model}"]` (Bug Fix
    Plan Phase 2.2) — the same map LLM Gateway's rate-limit self-heal path
    (Phase 2.1) writes to automatically, exposed here so a known free-tier
    ceiling can be entered before the first 429 rather than learned after it.
    `model` is the bare model name (the form the Settings panel already
    splits a stored `provider/model` string into); `save_model_budget`
    rejoins it with `provider` to build the full key `_resolve_tier` looks
    up — never key this map by the bare model name."""
    if body.budget <= 0:
        raise HTTPException(status_code=422, detail="budget must be a positive number of tokens")
    async with db.session() as session:
        await settings.save_model_budget(session, body.provider, body.model, body.budget)
        return await settings.get_settings(session)


class DiscoverModelsRequest(BaseModel):
    provider: Literal["ollama", "vllm"]
    base_url: str


class DiscoverModelsResponse(BaseModel):
    models: list[str]


@router.post("/api/settings/models/discover", response_model=DiscoverModelsResponse)
async def discover_models(body: DiscoverModelsRequest) -> DiscoverModelsResponse:
    try:
        models = await settings.discover_models(body.provider, body.base_url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"could not reach {body.provider}: {exc}") from exc
    return DiscoverModelsResponse(models=models)


class VaultPathRequest(BaseModel):
    path: str


@router.put("/api/settings/vault-path")
async def put_vault_path(body: VaultPathRequest) -> dict[str, str]:
    """Onboarding step 2. Takes effect on the *next* launch (see settings.set_vault_path)."""
    settings.set_vault_path(Path(body.path))
    return {"path": body.path}


@router.put("/api/settings/onboarding-complete", response_model=ModelSettings)
async def put_onboarding_complete() -> ModelSettings:
    """The wizard's last step; the app returns to step 1 while this is unset (D35)."""
    async with db.session() as session:
        await settings.complete_onboarding(session)
        return await settings.get_settings(session)


class VoiceSettings(BaseModel):
    # The full `api_keys.voice_engine` CHECK list (Schema.md) — `whisper_cpp`
    # has no registered engine (V11's "door open, nothing more") so it never
    # appears as a value in practice, but this mirrors the column's actual
    # contract rather than only what `PutVoiceSettingsRequest` below lets the
    # UI choose.
    voice_engine: Literal["stub", "faster_whisper", "whisper_cpp"]
    voice_ptt_binding: str


class PutVoiceSettingsRequest(BaseModel):
    # V10: the selector offers only the two profiles with a real engine
    # behind them. Both optional — the engine and the talk-key binding are
    # independent controls on the same panel, so either can be changed alone.
    voice_engine: Literal["stub", "faster_whisper"] | None = None
    voice_ptt_binding: str | None = None


@router.get("/api/settings/voice", response_model=VoiceSettings)
async def get_voice_settings() -> VoiceSettings:
    async with db.session() as session:
        return VoiceSettings(
            voice_engine=await settings.get_voice_engine(session),
            voice_ptt_binding=await settings.get_ptt_binding(session),
        )


@router.put("/api/settings/voice", response_model=VoiceSettings)
async def put_voice_settings(body: PutVoiceSettingsRequest) -> VoiceSettings:
    async with db.session() as session:
        if body.voice_engine is not None:
            await settings.set_voice_engine(session, body.voice_engine)
            # Takes effect on this very call, not the next restart (Voice.5
            # acceptance: "the change takes effect without a restart").
            voice.invalidate_engine_cache()
        if body.voice_ptt_binding is not None:
            await settings.set_ptt_binding(session, body.voice_ptt_binding)
        return VoiceSettings(
            voice_engine=await settings.get_voice_engine(session),
            voice_ptt_binding=await settings.get_ptt_binding(session),
        )
