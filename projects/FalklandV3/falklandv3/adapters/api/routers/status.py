"""Status endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from falklandv3.adapters.api.dependencies import runtime_dep
from falklandv3.api.schemas.status import StatusSnapshot
from falklandv3.services.runtime import GameRuntime


router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status", response_model=StatusSnapshot)
def get_status(runtime: GameRuntime = Depends(runtime_dep)) -> StatusSnapshot:
    return StatusSnapshot.model_validate(runtime.snapshot())
