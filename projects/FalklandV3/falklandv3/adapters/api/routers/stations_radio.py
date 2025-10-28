"""Radio station endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from falklandv3.adapters.api.dependencies import runtime_dep
from falklandv3.api.schemas.stations import RadioStationSnapshot
from falklandv3.services.runtime import GameRuntime
from falklandv3.stations import build_radio_station_view

router = APIRouter(prefix="/api/stations/radio", tags=["stations", "radio"])


@router.get("", response_model=RadioStationSnapshot)
def get_radio_station(
    limit: int = Query(20, ge=0, le=200, description="Maximum number of radio messages to include."),
    runtime: GameRuntime = Depends(runtime_dep),
) -> RadioStationSnapshot:
    snapshot = runtime.snapshot()
    view = build_radio_station_view(snapshot, limit=limit)
    return RadioStationSnapshot.model_validate(view.as_dict())
