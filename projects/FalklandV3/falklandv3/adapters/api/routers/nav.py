"""Navigation command endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from falklandv3.adapters.api.dependencies import runtime_dep
from falklandv3.adapters.api.auth import require_api_key
from falklandv3.api.schemas.commands import CourseCommand, SpeedCommand
from falklandv3.api.schemas.status import NavHistorySnapshot, StatusSnapshot
from falklandv3.services.runtime import GameRuntime


router = APIRouter(prefix="/api/nav", tags=["nav"])


@router.post("/course", response_model=StatusSnapshot, dependencies=[Depends(require_api_key)])
def post_course(cmd: CourseCommand, runtime: GameRuntime = Depends(runtime_dep)) -> StatusSnapshot:
    runtime.set_course(cmd.heading_deg)
    return StatusSnapshot.model_validate(runtime.snapshot())


@router.post("/speed", response_model=StatusSnapshot, dependencies=[Depends(require_api_key)])
def post_speed(cmd: SpeedCommand, runtime: GameRuntime = Depends(runtime_dep)) -> StatusSnapshot:
    runtime.set_speed(cmd.speed_kts)
    return StatusSnapshot.model_validate(runtime.snapshot())


@router.get("/history", response_model=NavHistorySnapshot)
def get_nav_history(runtime: GameRuntime = Depends(runtime_dep)) -> NavHistorySnapshot:
    history = runtime.snapshot()["nav_history"]
    return NavHistorySnapshot(**history)
