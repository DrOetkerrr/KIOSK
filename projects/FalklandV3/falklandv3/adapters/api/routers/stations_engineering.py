"""Engineering station endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from falklandv3.adapters.api.dependencies import runtime_dep
from falklandv3.api.schemas.stations import EngineeringStationSnapshot
from falklandv3.services.runtime import GameRuntime
from falklandv3.stations import build_engineering_station_view

router = APIRouter(prefix="/api/stations/engineering", tags=["stations", "engineering"])


@router.get("", response_model=EngineeringStationSnapshot)
def get_engineering_station(runtime: GameRuntime = Depends(runtime_dep)) -> EngineeringStationSnapshot:
    snapshot = runtime.snapshot()
    view = build_engineering_station_view(snapshot)
    return EngineeringStationSnapshot.model_validate(view.as_dict())
