"""API key authentication dependency."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from falklandv3.adapters.api.dependencies import runtime_dep
from falklandv3.services.runtime import GameRuntime

api_key_header = APIKeyHeader(name="X-Falkland-Key", auto_error=False)


async def require_api_key(
    api_key: Optional[str] = Security(api_key_header),
    runtime: GameRuntime = Depends(runtime_dep),
) -> None:
    expected = runtime.settings.api_key
    if expected is None:
        return
    if api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
