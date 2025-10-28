"""Weapons station projection utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class WeaponSlotEntry:
    """Single weapon slot entry with derived metadata."""

    name: str
    state: str
    armed: bool
    ammo: int
    max_ammo: int
    min_range_nm: Optional[float]
    max_range_nm: Optional[float]
    supports: Tuple[str, ...]
    ammo_per_shot: int
    category: str
    cooldown_remaining_s: float = 0.0

    @property
    def ammo_pct(self) -> float:
        if self.max_ammo <= 0:
            return 0.0
        return round((self.ammo / self.max_ammo) * 100.0, 1)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "armed": self.armed,
            "ammo": self.ammo,
            "max_ammo": self.max_ammo,
            "min_range_nm": self.min_range_nm,
            "max_range_nm": self.max_range_nm,
            "supports": list(self.supports),
            "ammo_per_shot": self.ammo_per_shot,
            "category": self.category,
            "ammo_pct": self.ammo_pct,
            "cooldown_remaining_s": self.cooldown_remaining_s,
        }


@dataclass(frozen=True)
class WeaponsStationView:
    """Projection for the weapons station UI."""

    slots: Sequence[WeaponSlotEntry]
    armed_count: int
    safe_count: int
    total_slots: int
    low_ammo_count: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "slots": [slot.as_dict() for slot in self.slots],
            "armed_count": self.armed_count,
            "safe_count": self.safe_count,
            "total_slots": self.total_slots,
            "low_ammo_count": self.low_ammo_count,
        }


def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _coerce_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalise_slots(entries: Iterable[Mapping[str, Any]]) -> List[WeaponSlotEntry]:
    normalised: List[WeaponSlotEntry] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        name = _coerce_str(entry.get("name"))
        state = _coerce_str(entry.get("state"), "Safe")
        armed = state.lower() == "armed"
        slot = WeaponSlotEntry(
            name=name,
            state=state,
            armed=armed,
            ammo=_coerce_int(entry.get("ammo"), 0),
            max_ammo=max(_coerce_int(entry.get("max_ammo"), 0), 0),
            min_range_nm=_coerce_float(entry.get("min_range_nm")),
            max_range_nm=_coerce_float(entry.get("max_range_nm")),
            supports=tuple(_coerce_str(target) for target in entry.get("supports", []) if target),
            ammo_per_shot=max(_coerce_int(entry.get("ammo_per_shot"), 1), 1),
            category=_coerce_str(entry.get("category"), ""),
            cooldown_remaining_s=max(0.0, _coerce_float(entry.get("cooldown_remaining_s")) or 0.0),
        )
        normalised.append(slot)
    normalised.sort(key=lambda item: item.name)
    return normalised


def build_weapons_station_view(snapshot: Mapping[str, Any]) -> WeaponsStationView:
    """Project runtime snapshot to weapons station view."""

    payload = snapshot.get("weapons") if isinstance(snapshot, Mapping) else None
    slot_entries = payload.get("slots", []) if isinstance(payload, Mapping) else []
    slots = _normalise_slots(slot_entries)
    armed = sum(1 for slot in slots if slot.armed)
    safe = sum(1 for slot in slots if not slot.armed)
    low_ammo = sum(1 for slot in slots if slot.max_ammo > 0 and slot.ammo / slot.max_ammo <= 0.25)

    return WeaponsStationView(
        slots=tuple(slots),
        armed_count=armed,
        safe_count=safe,
        total_slots=len(slots),
        low_ammo_count=low_ammo,
    )
