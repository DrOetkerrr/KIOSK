"""NAV station endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from falklandv3.adapters.api.dependencies import runtime_dep
from falklandv3.api.schemas.stations import NavStationSnapshot
from falklandv3.services.runtime import GameRuntime
from falklandv3.stations import build_nav_station_view

router = APIRouter(prefix="/api/stations/nav", tags=["stations", "nav"])


@router.get("", response_model=NavStationSnapshot)
def get_nav_station(
    limit: int = Query(10, ge=0, le=100, description="Maximum number of history entries to include."),
    runtime: GameRuntime = Depends(runtime_dep),
) -> NavStationSnapshot:
    snapshot = runtime.snapshot()
    view = build_nav_station_view(snapshot, history_limit=limit, tick_dt=runtime.settings.tick_dt)
    return NavStationSnapshot.model_validate(view.as_dict())
