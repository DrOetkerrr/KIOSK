"""Command payload schemas for Falkland V3 API."""

from __future__ import annotations

from pydantic import BaseModel


class CourseCommand(BaseModel):
    heading_deg: float


class SpeedCommand(BaseModel):
    speed_kts: float


class WeaponCommand(BaseModel):
    name: str


class WeaponFireCommand(BaseModel):
    name: str
    mode: str = "real"


class MissionDecisionCommand(BaseModel):
    decision_id: str
    choice: str


class RadarLockCommand(BaseModel):
    contact_id: int
