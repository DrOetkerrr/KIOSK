"""CAP-related API schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CAPStatusSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    sorties: int
    time_in_status_s: float
