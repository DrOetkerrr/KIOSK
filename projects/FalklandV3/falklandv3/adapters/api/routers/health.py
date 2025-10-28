"""Health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from falklandv3.adapters.api.dependencies import runtime_dep
from falklandv3.api.schemas.health import HealthSnapshot
from falklandv3.config import load_settings
from falklandv3.services.runtime import GameRuntime

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthSnapshot)
def get_health(runtime: GameRuntime = Depends(runtime_dep)) -> HealthSnapshot:
    settings = runtime.settings if hasattr(runtime, "settings") else load_settings()
    payload = {
        "status": "ok",
        "uptime_s": runtime.uptime_seconds() if hasattr(runtime, "uptime_seconds") else 0.0,
        "tick_dt": settings.tick_dt,
        "build": settings.build_label,
    }
    return HealthSnapshot(**payload)
