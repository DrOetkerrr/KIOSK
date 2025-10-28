"""Health endpoint schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class HealthSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    uptime_s: float
    tick_dt: float
    build: str
