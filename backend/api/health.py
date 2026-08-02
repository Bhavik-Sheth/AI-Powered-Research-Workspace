"""`GET /api/health` — backs Sidecar Bootstrap's per-capability readiness map (TRD §2.2).

The map itself lives on `app.state.readiness`, populated by main.py's
lifespan as each startup step completes — this route only reads it.
"""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

Capability = Literal["vault", "database", "docker", "llm", "search", "embeddings", "reranker", "voice"]
ReadinessState = Literal["pending", "ready", "failed"]

router = APIRouter()


class HealthResponse(BaseModel):
    capabilities: dict[Capability, ReadinessState]


@router.get("/api/health", response_model=HealthResponse)
async def get_health(request: Request) -> HealthResponse:
    return HealthResponse(capabilities=request.app.state.readiness)
