"""Engineering station projection utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class EngineeringAssetEntry:
    name: str
    lives: int
    max_lives: int
    status: str
    percent: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "lives": self.lives,
            "max_lives": self.max_lives,
            "status": self.status,
            "percent": self.percent,
        }


@dataclass(frozen=True)
class EngineeringTelemetry:
    wind_dir_deg: float
    wind_speed_kts: float
    sea_state: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "wind_dir_deg": self.wind_dir_deg,
            "wind_speed_kts": self.wind_speed_kts,
            "sea_state": self.sea_state,
        }


@dataclass(frozen=True)
class EngineeringStationView:
    assets: Sequence[EngineeringAssetEntry]
    critical_assets: Sequence[EngineeringAssetEntry]
    weather: Optional[EngineeringTelemetry]
    damage_alert: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "assets": [asset.as_dict() for asset in self.assets],
            "critical_assets": [asset.as_dict() for asset in self.critical_assets],
            "weather": self.weather.as_dict() if self.weather else None,
            "damage_alert": self.damage_alert,
        }


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _normalise_assets(entries: Iterable[Mapping[str, Any]]) -> List[EngineeringAssetEntry]:
    normalised: List[EngineeringAssetEntry] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        name = _coerce_str(entry.get("name"))
        max_lives = max(0, _coerce_int(entry.get("max_lives")))
        lives = min(max_lives, max(0, _coerce_int(entry.get("lives"))))
        percent = (lives / max_lives * 100.0) if max_lives > 0 else 0.0
        status = "critical" if percent <= 25 else "warning" if percent <= 50 else "nominal"
        normalised.append(
            EngineeringAssetEntry(
                name=name,
                lives=lives,
                max_lives=max_lives,
                status=status,
                percent=round(percent, 1),
            )
        )
    normalised.sort(key=lambda asset: asset.name)
    return normalised


def _build_weather(weather_payload: Optional[Mapping[str, Any]]) -> Optional[EngineeringTelemetry]:
    if not isinstance(weather_payload, Mapping):
        return None
    return EngineeringTelemetry(
        wind_dir_deg=_coerce_float(weather_payload.get("wind_dir_deg")),
        wind_speed_kts=_coerce_float(weather_payload.get("wind_speed_kts")),
        sea_state=_coerce_float(weather_payload.get("sea_state")),
    )


def build_engineering_station_view(snapshot: Mapping[str, Any]) -> EngineeringStationView:
    """Project runtime snapshot to engineering station view."""

    health_payload = snapshot.get("health") if isinstance(snapshot, Mapping) else None
    asset_entries = health_payload.get("assets", []) if isinstance(health_payload, Mapping) else []
    assets = _normalise_assets(asset_entries)
    critical_assets = tuple(asset for asset in assets if asset.status == "critical")
    weather_view = _build_weather(snapshot.get("weather") if isinstance(snapshot, Mapping) else None)
    damage_alert = any(asset.status != "nominal" for asset in assets)

    return EngineeringStationView(
        assets=tuple(assets),
        critical_assets=critical_assets,
        weather=weather_view,
        damage_alert=damage_alert,
    )
