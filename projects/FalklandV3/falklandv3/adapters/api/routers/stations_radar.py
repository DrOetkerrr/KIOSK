"""RADAR station endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from falklandv3.adapters.api.dependencies import runtime_dep
from falklandv3.api.schemas.stations import RadarStationSnapshot
from falklandv3.services.runtime import GameRuntime
from falklandv3.stations import build_radar_station_view

router = APIRouter(prefix="/api/stations/radar", tags=["stations", "radar"])


@router.get("", response_model=RadarStationSnapshot)
def get_radar_station(runtime: GameRuntime = Depends(runtime_dep)) -> RadarStationSnapshot:
    snapshot = runtime.snapshot()
    max_contacts = getattr(runtime.radar, "max_contacts", None)
    view = build_radar_station_view(snapshot, max_contacts=max_contacts)
    return RadarStationSnapshot.model_validate(view.as_dict())
