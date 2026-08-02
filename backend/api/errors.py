"""The one error envelope every REST error carries (Rules.md).

Built by a single FastAPI exception handler; routers never construct it by
hand. This is one of the two sanctioned bare-`Exception` catch sites in the
backend (Rules.md) — every other unhandled exception is a bug, this one
exists to guarantee the envelope shape even for the ones nobody anticipated.
"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    recoverable: bool
    what_still_worked: str | None = None


async def handle_exception(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("event=unhandled_exception path=%s", request.url.path)
    envelope = ErrorEnvelope(code=type(exc).__name__, message=str(exc), recoverable=False)
    return JSONResponse(status_code=500, content=envelope.model_dump())
