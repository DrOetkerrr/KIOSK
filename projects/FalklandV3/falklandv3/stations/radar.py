"""RADAR station projection utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class RadarContactEntry:
    """Single radar contact entry for station display."""

    id: int
    label: str
    allegiance: str
    range_nm: float
    bearing_deg: float
    heading_deg: float
    speed_kts: float
    hostile: bool
    priority: int
    category: Optional[str]
    primary_weapon: Optional[str]
    cell: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "allegiance": self.allegiance,
            "range_nm": self.range_nm,
            "bearing_deg": self.bearing_deg,
            "heading_deg": self.heading_deg,
            "speed_kts": self.speed_kts,
            "hostile": self.hostile,
            "priority": self.priority,
            "category": self.category,
            "primary_weapon": self.primary_weapon,
            "cell": self.cell,
        }


@dataclass(frozen=True)
class RadarWaveInfo:
    label: str
    elapsed_s: float
    duration_s: float
    remaining_s: Optional[float]
    spawn_rate_per_min: float
    friendly_prob: float
    direction_bearing: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "elapsed_s": self.elapsed_s,
            "duration_s": self.duration_s,
            "remaining_s": self.remaining_s,
            "spawn_rate_per_min": self.spawn_rate_per_min,
            "friendly_prob": self.friendly_prob,
            "direction_bearing": self.direction_bearing,
        }


@dataclass(frozen=True)
class RadarStationView:
    """Presentation view for the RADAR station."""

    contacts: Sequence[RadarContactEntry]
    hostile_count: int
    friendly_count: int
    max_contacts: int
    wave: Optional[RadarWaveInfo]
    locked_contact_id: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "contacts": [entry.as_dict() for entry in self.contacts],
            "hostile_count": self.hostile_count,
            "friendly_count": self.friendly_count,
            "max_contacts": self.max_contacts,
            "locked_contact_id": self.locked_contact_id,
            "wave": self.wave.as_dict() if self.wave else None,
        }


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _normalise_contacts(entries: Iterable[Mapping[str, Any]]) -> List[RadarContactEntry]:
    normalised: List[RadarContactEntry] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        allegiance = _coerce_str(entry.get("allegiance")).capitalize()
        range_nm = max(0.0, _coerce_float(entry.get("range_nm")))
        hostile = allegiance.lower() == "hostile"
        category = _coerce_str(entry.get("category"), None)
        primary_weapon = _coerce_str(entry.get("primary_weapon"), None)
        cell = _coerce_str(entry.get("cell")) or _coerce_str(entry.get("grid"), None)
        priority = _compute_priority(range_nm, hostile, category, primary_weapon, _coerce_str(entry.get("label")))
        normalised.append(
            RadarContactEntry(
                id=_coerce_int(entry.get("id")),
                label=_coerce_str(entry.get("label")),
                allegiance=allegiance,
                range_nm=range_nm,
                bearing_deg=_coerce_float(entry.get("bearing_deg")),
                heading_deg=_coerce_float(entry.get("heading_deg")),
                speed_kts=_coerce_float(entry.get("speed_kts")),
                hostile=hostile,
                priority=priority,
                category=category,
                primary_weapon=primary_weapon,
                cell=cell or "AA00",
            )
        )
    normalised.sort(key=lambda item: (item.priority, item.range_nm, item.id))
    return normalised


def _compute_priority(range_nm: float, hostile: bool, category: Optional[str], primary_weapon: Optional[str], label: str) -> int:
    if hostile:
        weapon = (primary_weapon or "").lower()
        cat = (category or "").lower()
        label_lower = label.lower()
        if "exocet" in weapon or "exocet" in label_lower or cat == "missile":
            return -1
        if range_nm <= 5.0:
            return 0
        if range_nm <= 12.0:
            return 1
        return 2
    return 3


def _build_wave(payload: Optional[Mapping[str, Any]]) -> Optional[RadarWaveInfo]:
    if not isinstance(payload, Mapping):
        return None
    label = _coerce_str(payload.get("label"))
    elapsed = _coerce_float(payload.get("elapsed_s"))
    duration = _coerce_float(payload.get("duration_s"))
    remaining_raw = payload.get("remaining_s")
    remaining = _coerce_float(remaining_raw) if remaining_raw is not None else None
    return RadarWaveInfo(
        label=label,
        elapsed_s=elapsed,
        duration_s=duration,
        remaining_s=remaining,
        spawn_rate_per_min=_coerce_float(payload.get("spawn_rate_per_min")),
        friendly_prob=_coerce_float(payload.get("friendly_prob")),
        direction_bearing=_coerce_float(payload.get("direction_bearing")),
    )


def build_radar_station_view(snapshot: Mapping[str, Any], *, max_contacts: int | None = None) -> RadarStationView:
    """Project runtime snapshot to RADAR station view."""

    radar_payload = snapshot.get("radar") if isinstance(snapshot, Mapping) else None
    contact_entries = radar_payload.get("contacts", []) if isinstance(radar_payload, Mapping) else []
    contacts = _normalise_contacts(contact_entries)
    locked_id_raw = None
    if isinstance(radar_payload, Mapping):
        locked_id_raw = radar_payload.get("locked_contact_id") or radar_payload.get("locked_id")
    try:
        locked_id = int(locked_id_raw) if locked_id_raw is not None else None
    except (TypeError, ValueError):
        locked_id = None

    hostile_count = sum(1 for entry in contacts if entry.hostile)
    friendly_count = sum(1 for entry in contacts if not entry.hostile)
    capacity = max_contacts if isinstance(max_contacts, int) and max_contacts > 0 else len(contacts)

    wave_payload = snapshot.get("wave") if isinstance(snapshot, Mapping) else None
    wave = _build_wave(wave_payload if isinstance(wave_payload, Mapping) else None)

    return RadarStationView(
        contacts=tuple(contacts),
        hostile_count=hostile_count,
        friendly_count=friendly_count,
        max_contacts=capacity,
        wave=wave,
        locked_contact_id=locked_id,
    )
