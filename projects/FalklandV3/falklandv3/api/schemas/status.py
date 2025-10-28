"""Pydantic models describing Falkland V3 status payloads."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class ShipSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cell: str
    x_nm: float
    y_nm: float
    heading_deg: float
    speed_kts: float
    hud: str


class RadarContactSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    allegiance: str
    range_nm: float
    bearing_deg: float
    heading_deg: float
    speed_kts: float
    category: Optional[str] = None
    primary_weapon: Optional[str] = None
    cell: str


class RadarSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    contacts: List[RadarContactSnapshot]
    locked_contact_id: Optional[int] = None


class MissionSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    description: str
    status: str
    elapsed_s: float
    time_left_s: Optional[float]
    decision: Optional[Dict[str, Any]] = None


class WaveSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str
    elapsed_s: float
    duration_s: float
    remaining_s: Optional[float]
    spawn_rate_per_min: float
    friendly_prob: float
    direction_bearing: float


class CAPSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    sorties: int
    time_in_status_s: float
    harriers: List["SeaHarrierStatusSnapshot"]


class WeaponSlotSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    state: str
    ammo: int
    max_ammo: int
    min_range_nm: Optional[float] = None
    max_range_nm: Optional[float] = None
    supports: List[str]
    ammo_per_shot: int
    category: str
    cooldown_remaining_s: float


class WeaponsSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slots: List[WeaponSlotSnapshot]


class AudioEventSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    message: str
    ts: float


class ShotInFlightSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    weapon: str
    target: str
    cell: str
    range_nm: float
    pk_pct: int
    eta_s: float
    result: Optional[str] = None
    mode: str


class AudioSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    events: List[AudioEventSnapshot]
    shots_in_flight: List[ShotInFlightSnapshot]


class WeatherSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    wind_dir_deg: float
    wind_speed_kts: float
    sea_state: float


class RadioMessageSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    text: str
    category: str
    ts: float


class RadioSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    messages: List[RadioMessageSnapshot]


class SeaHarrierStatusSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    callsign: str
    status: str
    fuel_pct: float
    time_in_status_s: float


class NavCommandSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: float
    action: str
    value: float


class NavHistorySnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entries: List[NavCommandSnapshot]


class CapHistoryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: float
    action: str
    sorties: int
    mission_status: str


class CapHistorySnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entries: List[CapHistoryEntry]


class HealthAssetSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    max_lives: int
    lives: int


class HealthSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    assets: List[HealthAssetSnapshot]


class StatusSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ship: ShipSnapshot
    radar: RadarSnapshot
    mission: MissionSnapshot
    cap: CAPSnapshot
    wave: WaveSnapshot
    weapons: WeaponsSnapshot
    audio: AudioSnapshot
    weather: WeatherSnapshot
    radio: RadioSnapshot
    nav_history: NavHistorySnapshot
    cap_history: CapHistorySnapshot
    health: HealthSnapshot
