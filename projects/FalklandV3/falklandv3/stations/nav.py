"""NAV station projection utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence


@dataclass(frozen=True)
class NavHistoryEntry:
    """Single navigation command entry."""

    id: int
    ts: float
    action: str
    value: float

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "ts": self.ts, "action": self.action, "value": self.value}


@dataclass(frozen=True)
class NavStationView:
    """Presentation model for the NAV station."""

    hud: str
    heading_deg: float
    speed_kts: float
    cell: str
    x_nm: float
    y_nm: float
    tick_dt: float
    history_total: int
    history: Sequence[NavHistoryEntry]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "hud": self.hud,
            "heading_deg": self.heading_deg,
            "speed_kts": self.speed_kts,
            "cell": self.cell,
            "x_nm": self.x_nm,
            "y_nm": self.y_nm,
            "tick_dt": self.tick_dt,
            "history_total": self.history_total,
            "history": [entry.as_dict() for entry in self.history],
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


def _normalise_history(entries: Iterable[Mapping[str, Any]]) -> List[NavHistoryEntry]:
    normalised: List[NavHistoryEntry] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        normalised.append(
            NavHistoryEntry(
                id=_coerce_int(entry.get("id")),
                ts=_coerce_float(entry.get("ts")),
                action=_coerce_str(entry.get("action")),
                value=_coerce_float(entry.get("value")),
            )
        )
    normalised.sort(key=lambda item: item.ts, reverse=True)
    return normalised


def build_nav_station_view(
    snapshot: Mapping[str, Any],
    *,
    history_limit: int = 10,
    tick_dt: float = 1.0,
) -> NavStationView:
    """Project the runtime snapshot into a NAV station view."""

    history_payload = snapshot.get("nav_history") if isinstance(snapshot, Mapping) else None
    history_entries = history_payload.get("entries", []) if isinstance(history_payload, Mapping) else []
    normalised = _normalise_history(history_entries)
    limited = normalised[: max(0, history_limit)]

    ship_payload = snapshot.get("ship") if isinstance(snapshot, Mapping) else None
    ship = ship_payload if isinstance(ship_payload, Mapping) else {}

    return NavStationView(
        hud=_coerce_str(ship.get("hud")),
        heading_deg=_coerce_float(ship.get("heading_deg")),
        speed_kts=_coerce_float(ship.get("speed_kts")),
        cell=_coerce_str(ship.get("cell")),
        x_nm=_coerce_float(ship.get("x_nm")),
        y_nm=_coerce_float(ship.get("y_nm")),
        tick_dt=_coerce_float(tick_dt, 1.0),
        history_total=len(normalised),
        history=tuple(limited),
    )
