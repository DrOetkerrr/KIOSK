"""Station projection schemas."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class NavStationHistoryEntryModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: float
    action: str
    value: float


class NavStationSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    hud: str
    heading_deg: float
    speed_kts: float
    cell: str
    x_nm: float
    y_nm: float
    tick_dt: float
    history_total: int
    history: List[NavStationHistoryEntryModel]


class RadarStationContactModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    allegiance: str
    range_nm: float
    bearing_deg: float
    heading_deg: float
    speed_kts: float
    hostile: bool
    priority: int
    category: Optional[str] = None
    primary_weapon: Optional[str] = None
    cell: str


class RadarStationWaveModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str
    elapsed_s: float
    duration_s: float
    remaining_s: Optional[float]
    spawn_rate_per_min: float
    friendly_prob: float
    direction_bearing: float


class RadarStationSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    contacts: List[RadarStationContactModel]
    hostile_count: int
    friendly_count: int
    max_contacts: int
    wave: Optional[RadarStationWaveModel]
    locked_contact_id: Optional[int] = None


class WeaponSlotModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    state: str
    armed: bool
    ammo: int
    max_ammo: int
    min_range_nm: Optional[float] = None
    max_range_nm: Optional[float] = None
    supports: List[str]
    ammo_per_shot: int
    category: str
    ammo_pct: float
    cooldown_remaining_s: float


class WeaponsStationSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slots: List[WeaponSlotModel]
    armed_count: int
    safe_count: int
    total_slots: int
    low_ammo_count: int


class RadioStationMessageModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    text: str
    category: str
    ts: float


class RadioStationSummaryModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category: str
    count: int


class RadioStationSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    messages: List[RadioStationMessageModel]
    summaries: List[RadioStationSummaryModel]
    total_messages: int


class EngineeringAssetModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    lives: int
    max_lives: int
    status: str
    percent: float


class EngineeringTelemetryModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    wind_dir_deg: float
    wind_speed_kts: float
    sea_state: float


class EngineeringStationSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    assets: List[EngineeringAssetModel]
    critical_assets: List[EngineeringAssetModel]
    weather: Optional[EngineeringTelemetryModel]
    damage_alert: bool
