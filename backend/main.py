"""Sidecar Bootstrap — process start to per-capability readiness, and back down (MODULES.md).

Owns the FastAPI app's lifespan and the launch ordering fixed by TRD §2.2:
vault check -> docker compose up -> Alembic migrations -> job worker ->
catch-up pass. Domain logic lives in the modules this file sequences; it
knows nothing about what any of them do internally.
"""

import asyncio
import logging
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

import db
import jobs
import vault
from api.deps import require_bearer_token
from api.errors import handle_exception, handle_http_exception, handle_validation_error
from api.health import Capability, ReadinessState
from api.health import router as health_router
from api.highlights import router as highlights_router
from api.notes import router as notes_router
from api.papers import router as papers_router
from api.projects import router as projects_router
from api.search import router as search_router
from api.settings import router as settings_router
from config import get_config
from ws import router as ws_router

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_COMPOSE_FILE = Path(__file__).parent.parent / "docker" / "docker-compose.yml"

_INITIAL_READINESS: dict[Capability, ReadinessState] = {
    "vault": "pending",
    "database": "pending",
    "docker": "pending",
    "llm": "pending",
    "search": "pending",
    "embeddings": "pending",
    "reranker": "pending",
    "voice": "pending",
}


def _mark_failed(readiness: dict[Capability, ReadinessState], capability: Capability) -> None:
    logger.exception("event=startup_step_failed capability=%s", capability)
    readiness[capability] = "failed"


async def _compose_up(vault_root: Path) -> None:
    """`docker compose up -d --wait` for Postgres+pgvector, scoped to this vault."""
    config = get_config()
    env = os.environ.copy()
    env.update(
        {
            "VAULT_PATH": str(vault_root),
            "POSTGRES_USER": config.postgres_user,
            "POSTGRES_PASSWORD": config.postgres_password,
            "POSTGRES_DB": config.postgres_db,
            "POSTGRES_PORT": str(config.postgres_port),
        }
    )
    process = await asyncio.create_subprocess_exec(
        "docker",
        "compose",
        "-f",
        str(_COMPOSE_FILE),
        "up",
        "-d",
        "--wait",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(output.decode(errors="replace"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    readiness: dict[Capability, ReadinessState] = dict(_INITIAL_READINESS)
    app.state.readiness = readiness

    vault_root: Path | None = None
    try:
        vault_root = vault.ensure_layout()
        readiness["vault"] = "ready"
    except OSError:
        _mark_failed(readiness, "vault")

    if vault_root is not None:
        docker_ready = False
        try:
            await _compose_up(vault_root)
            readiness["docker"] = "ready"
            docker_ready = True
        except (FileNotFoundError, RuntimeError):
            _mark_failed(readiness, "docker")

        if docker_ready:
            try:
                # Alembic/SQLAlchemy can raise from a wide range of internal
                # error types here; this step is the sanctioned aggregation
                # boundary for all of them (Rules.md Module Rules, Errors #3).
                await db.run_migrations()
                readiness["database"] = "ready"
                await jobs.start()
                await jobs.run_catchup_pass()
            except Exception:
                _mark_failed(readiness, "database")

    # uvicorn skips its own "started" log line when serving a pre-bound
    # socket (see `_serve`), so this is the one place that confirms readiness.
    logger.info("event=sidecar_ready capabilities=%s", readiness)

    yield

    logger.info("event=sidecar_shutdown_start")
    await jobs.stop()
    await db.dispose()
    logger.info("event=sidecar_shutdown_complete")


def create_app() -> FastAPI:
    app = FastAPI(title="Research Companion OS", version="0.1.0", lifespan=lifespan)
    # The renderer's origin (Vite's dev server, or Electron's `file://` in
    # production — `webSecurity` is not disabled there) is otherwise unable
    # to read any response. The bearer token (D2), not origin, is the access
    # boundary here, so allowing every origin adds no real exposure.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_exception)
    app.include_router(health_router, dependencies=[Depends(require_bearer_token)])
    app.include_router(settings_router, dependencies=[Depends(require_bearer_token)])
    app.include_router(projects_router, dependencies=[Depends(require_bearer_token)])
    app.include_router(notes_router, dependencies=[Depends(require_bearer_token)])
    app.include_router(highlights_router, dependencies=[Depends(require_bearer_token)])
    app.include_router(papers_router, dependencies=[Depends(require_bearer_token)])
    app.include_router(search_router, dependencies=[Depends(require_bearer_token)])
    app.include_router(ws_router)
    return app


app = create_app()


async def _serve() -> None:
    """Binds 127.0.0.1:0 and prints the bound port on stdout for Electron main (D2).

    The socket is bound here, up front, rather than left to uvicorn's own
    `port=0` handling, so the bound port is known and printed before serving
    starts — `Server.serve(sockets=...)` is the documented way to hand
    uvicorn an already-bound socket (used for socket-activation deployments).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    bound_port = sock.getsockname()[1]
    print(bound_port, flush=True)

    # log_config=None: uvicorn's default dictConfig has disable_existing_loggers
    # True, which would silently disable every logger this module tree uses;
    # defer entirely to the logging.basicConfig() set up above instead.
    config = uvicorn.Config(app, host="127.0.0.1", port=bound_port, log_level="info", log_config=None)
    server = uvicorn.Server(config)
    await server.serve(sockets=[sock])


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
