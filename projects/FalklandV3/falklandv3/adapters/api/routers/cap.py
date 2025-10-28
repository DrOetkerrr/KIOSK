"""CAP endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from falklandv3.adapters.api.dependencies import runtime_dep
from falklandv3.api.schemas.cap import CAPStatusSnapshot
from falklandv3.api.schemas.status import StatusSnapshot
from falklandv3.services.runtime import GameRuntime


router = APIRouter(prefix="/api/cap", tags=["cap"])


@router.get("/status", response_model=CAPStatusSnapshot)
def get_cap_status(runtime: GameRuntime = Depends(runtime_dep)) -> CAPStatusSnapshot:
    snapshot = runtime.snapshot()["cap"]
    return CAPStatusSnapshot(**snapshot)


@router.post("/launch", response_model=StatusSnapshot)
def post_cap_launch(runtime: GameRuntime = Depends(runtime_dep)) -> StatusSnapshot:
    runtime.launch_cap()
    return StatusSnapshot.model_validate(runtime.snapshot())


@router.post("/reset", response_model=CAPStatusSnapshot)
def post_cap_reset(runtime: GameRuntime = Depends(runtime_dep)) -> CAPStatusSnapshot:
    runtime.reset_cap()
    snapshot = runtime.snapshot()["cap"]
    return CAPStatusSnapshot(**snapshot)
