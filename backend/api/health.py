"""`GET /api/health` — backs Sidecar Bootstrap's per-capability readiness map (TRD §2.2).

The map is a single process-wide dict owned by this module. `main.py`'s
lifespan mutates it as each startup step completes; Voice.4's background
weight-fetch job does the same afterwards, off the request path, flipping
`voice` from `pending` to `ready`/`failed` once it finishes — a per-request
`app.state` copy would leave a background job with no way to reach it, and
there is exactly one FastAPI app per process (D2), so one module-level dict
is the whole story.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

Capability = Literal["vault", "database", "docker", "llm", "search", "embeddings", "reranker", "voice"]
ReadinessState = Literal["pending", "ready", "failed"]

readiness: dict[Capability, ReadinessState] = {
    "vault": "pending",
    "database": "pending",
    "docker": "pending",
    "llm": "pending",
    "search": "pending",
    "embeddings": "pending",
    "reranker": "pending",
    "voice": "pending",
}

router = APIRouter()


class HealthResponse(BaseModel):
    capabilities: dict[Capability, ReadinessState]


@router.get("/api/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    return HealthResponse(capabilities=readiness)
