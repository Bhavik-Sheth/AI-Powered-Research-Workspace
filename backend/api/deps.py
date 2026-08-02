"""The bearer-token dependency — mandatory on every REST request (D2).

Loopback is not an authorization boundary; a request without the per-launch
token gets 401 before any router runs. There is no user auth beyond this.
"""

from fastapi import Header, HTTPException

from config import get_config


async def require_bearer_token(authorization: str | None = Header(default=None)) -> None:
    if authorization != f"Bearer {get_config().bearer_token}":
        raise HTTPException(status_code=401, detail="unauthorized")
