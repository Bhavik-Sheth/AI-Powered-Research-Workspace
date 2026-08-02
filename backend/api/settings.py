"""`GET/PUT /api/settings/models` — backs Settings Store (TRD §4.2)."""

from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
import settings
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
