"""Radar command endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from falklandv3.adapters.api.auth import require_api_key
from falklandv3.adapters.api.dependencies import runtime_dep
from falklandv3.api.schemas.commands import RadarLockCommand
from falklandv3.api.schemas.status import StatusSnapshot
from falklandv3.services.runtime import GameRuntime

router = APIRouter(prefix="/api/radar", tags=["radar"])


@router.post("/lock", response_model=StatusSnapshot, dependencies=[Depends(require_api_key)])
def post_radar_lock(cmd: RadarLockCommand, runtime: GameRuntime = Depends(runtime_dep)) -> StatusSnapshot:
    ok = runtime.lock_radar_contact(cmd.contact_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Radar contact not found")
    return StatusSnapshot.model_validate(runtime.snapshot())


@router.post("/unlock", response_model=StatusSnapshot, dependencies=[Depends(require_api_key)])
def post_radar_unlock(runtime: GameRuntime = Depends(runtime_dep)) -> StatusSnapshot:
    runtime.unlock_radar_contact()
    return StatusSnapshot.model_validate(runtime.snapshot())
