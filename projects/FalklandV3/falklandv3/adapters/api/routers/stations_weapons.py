"""Weapons station endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from falklandv3.adapters.api.dependencies import runtime_dep
from falklandv3.api.schemas.stations import WeaponsStationSnapshot
from falklandv3.services.runtime import GameRuntime
from falklandv3.stations import build_weapons_station_view

router = APIRouter(prefix="/api/stations/weapons", tags=["stations", "weapons"])


@router.get("", response_model=WeaponsStationSnapshot)
def get_weapons_station(runtime: GameRuntime = Depends(runtime_dep)) -> WeaponsStationSnapshot:
    snapshot = runtime.snapshot()
    view = build_weapons_station_view(snapshot)
    return WeaponsStationSnapshot.model_validate(view.as_dict())
