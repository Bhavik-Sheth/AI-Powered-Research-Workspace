"""The one error envelope every REST error carries (Rules.md).

Built by these handlers alone; routers raise `HTTPException` (for an
expected, recoverable failure — e.g. "invalid key") or let an unexpected
exception propagate, and never construct the envelope by hand. Three
handlers cover the three shapes FastAPI/Starlette can produce: an explicit
`HTTPException`, a request that fails Pydantic validation, and everything
else — without all three, an `HTTPException` would bypass this envelope
entirely, since Starlette's own default handler otherwise wins over a
handler registered only for the base `Exception` type.
"""

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    recoverable: bool
    what_still_worked: str | None = None


_RECOVERABLE_GATEWAY_CODES = {502, 503, 504}


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # 4xx is a client-fixable request; 502/503/504 mean the *external* thing
    # (a provider endpoint, Docker) is temporarily unreachable, also worth a
    # retry. A bare 500 is the only case treated as not recoverable.
    recoverable = exc.status_code < 500 or exc.status_code in _RECOVERABLE_GATEWAY_CODES
    envelope = ErrorEnvelope(code=str(exc.status_code), message=str(exc.detail), recoverable=recoverable)
    return JSONResponse(status_code=exc.status_code, content=envelope.model_dump())


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    envelope = ErrorEnvelope(code="422", message=str(exc.errors()), recoverable=True)
    return JSONResponse(status_code=422, content=envelope.model_dump())


async def handle_exception(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("event=unhandled_exception path=%s", request.url.path)
    envelope = ErrorEnvelope(code=type(exc).__name__, message=str(exc), recoverable=False)
    return JSONResponse(status_code=500, content=envelope.model_dump())
