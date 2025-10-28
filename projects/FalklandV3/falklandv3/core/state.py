"""State reducer producing immutable runtime snapshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import Lock
from typing import Any, Dict, Optional, Sequence, Tuple

from falklandv3.core.engine import BOARD_MIN_X, BOARD_MIN_Y, ShipState
from falklandv3.core.events import RadarContactsUpdated, ShipMoved
from falklandv3.core.cap import CAPSnapshot
from falklandv3.core.radar import RadarContactView
from falklandv3.core.weather import WeatherState
from falklandv3.core.radio import RadioMessage
from falklandv3.core.nav_history import NavCommand
from falklandv3.core.cap_history import CapLogEntry
from falklandv3.core.health import AssetHealth
from falklandv3.core.audio import AudioEvent
from falklandv3.core.shar import SeaHarrierSnapshot
from falklandv3.core.weapons import WeaponSlot
from falklandv3.utils.grid import world_to_label


@dataclass(frozen=True)
class ShipView:
    cell: str
    x_nm: float
    y_nm: float
    heading_deg: float
    speed_kts: float
    hud: str


@dataclass(frozen=True)
class RuntimeSnapshot:
    ship: ShipView
    radar: "RadarView"
    wave: "WaveView"
    mission: "MissionView"
    cap: "CAPView"
    weapons: "WeaponsView"
    audio: "AudioView"
    weather: "WeatherView"
    radio: "RadioView"
    nav_history: "NavHistoryView"
    cap_history: "CapHistoryView"
    health: "HealthView"

    def as_dict(self) -> Dict[str, object]:
        return {
            "ship": asdict(self.ship),
            "radar": self.radar.as_dict(),
            "wave": self.wave.as_dict(),
            "mission": self.mission.as_dict(),
            "cap": self.cap.as_dict(),
            "weapons": self.weapons.as_dict(),
            "audio": self.audio.as_dict(),
            "weather": self.weather.as_dict(),
            "radio": self.radio.as_dict(),
            "nav_history": self.nav_history.as_dict(),
            "cap_history": self.cap_history.as_dict(),
            "health": self.health.as_dict(),
        }


@dataclass(frozen=True)
class RadarView:
    contacts: Tuple[RadarContactView, ...]
    locked_contact_id: Optional[int] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "contacts": [asdict(contact) for contact in self.contacts],
            "locked_contact_id": self.locked_contact_id,
        }


@dataclass(frozen=True)
class MissionView:
    mission_id: str
    label: str
    description: str
    status: str
    elapsed_s: float
    time_left_s: Optional[float]
    decision: Optional[Dict[str, object]]

    def as_dict(self) -> Dict[str, object]:
        return {
            "id": self.mission_id,
            "label": self.label,
            "description": self.description,
            "status": self.status,
            "elapsed_s": self.elapsed_s,
            "time_left_s": self.time_left_s,
            "decision": self.decision,
        }


@dataclass(frozen=True)
class WaveView:
    label: str
    elapsed_s: float
    duration_s: float
    remaining_s: Optional[float]
    spawn_rate_per_min: float
    friendly_prob: float
    direction_bearing: float

    def as_dict(self) -> Dict[str, object]:
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
class CAPView:
    status: str
    sorties: int
    time_in_status_s: float
    harriers: Tuple["SeaHarrierView", ...]

    def as_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "sorties": self.sorties,
            "time_in_status_s": self.time_in_status_s,
            "harriers": [harrier.as_dict() for harrier in self.harriers],
        }


@dataclass(frozen=True)
class WeaponSlotView:
    name: str
    state: str
    ammo: int
    max_ammo: int
    min_range_nm: Optional[float]
    max_range_nm: Optional[float]
    supports: Tuple[str, ...]
    ammo_per_shot: int
    category: str
    cooldown_remaining_s: float

    def as_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "state": self.state,
            "ammo": self.ammo,
            "max_ammo": self.max_ammo,
            "min_range_nm": self.min_range_nm,
            "max_range_nm": self.max_range_nm,
            "supports": list(self.supports),
            "ammo_per_shot": self.ammo_per_shot,
            "category": self.category,
            "cooldown_remaining_s": self.cooldown_remaining_s,
        }


@dataclass(frozen=True)
class WeaponsView:
    slots: Tuple[WeaponSlotView, ...]

    def as_dict(self) -> Dict[str, object]:
        return {"slots": [slot.as_dict() for slot in self.slots]}


@dataclass(frozen=True)
class AudioView:
    events: Tuple[Dict[str, object], ...]
    shots_in_flight: Tuple[Dict[str, object], ...]

    def as_dict(self) -> Dict[str, object]:
        return {
            "events": list(self.events),
            "shots_in_flight": list(self.shots_in_flight),
        }


@dataclass(frozen=True)
class WeatherView:
    wind_dir_deg: float
    wind_speed_kts: float
    sea_state: float

    def as_dict(self) -> Dict[str, object]:
        return {
            "wind_dir_deg": self.wind_dir_deg,
            "wind_speed_kts": self.wind_speed_kts,
            "sea_state": self.sea_state,
        }


@dataclass(frozen=True)
class RadioView:
    messages: Tuple[Dict[str, object], ...]

    def as_dict(self) -> Dict[str, object]:
        return {"messages": list(self.messages)}


@dataclass(frozen=True)
class NavHistoryView:
    entries: Tuple[Dict[str, Any], ...]

    def as_dict(self) -> Dict[str, object]:
        return {"entries": list(self.entries)}


@dataclass(frozen=True)
class CapHistoryView:
    entries: Tuple[Dict[str, Any], ...]

    def as_dict(self) -> Dict[str, object]:
        return {"entries": list(self.entries)}


@dataclass(frozen=True)
class HealthAssetView:
    name: str
    max_lives: int
    lives: int


@dataclass(frozen=True)
class SeaHarrierView:
    callsign: str
    status: str
    fuel_pct: float
    time_in_status_s: float

    def as_dict(self) -> Dict[str, object]:
        return {
            "callsign": self.callsign,
            "status": self.status,
            "fuel_pct": self.fuel_pct,
            "time_in_status_s": self.time_in_status_s,
        }


@dataclass(frozen=True)
class HealthView:
    assets: Tuple[HealthAssetView, ...]

    def as_dict(self) -> Dict[str, object]:
        return {
            "assets": [
                {"name": asset.name, "max_lives": asset.max_lives, "lives": asset.lives}
                for asset in self.assets
            ]
        }


def _grid_cell(x_nm: float, y_nm: float) -> str:
    return world_to_label(x_nm, y_nm)


def _hud_line(cell: str, heading_deg: float, speed_kts: float) -> str:
    return f"Ship {cell} | hdg {heading_deg:.0f}° spd {speed_kts:.0f} kn"


class StateReducer:
    """Thread-safe reducer maintaining the latest runtime snapshot."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._radar_locked_id: Optional[int] = None
        default_cell = _grid_cell(BOARD_MIN_X + 10.0, BOARD_MIN_Y + 12.0)
        self._snapshot = RuntimeSnapshot(
            ship=ShipView(
                cell=default_cell,
                x_nm=BOARD_MIN_X + 10.0,
                y_nm=BOARD_MIN_Y + 12.0,
                heading_deg=0.0,
                speed_kts=0.0,
                hud=_hud_line(default_cell, 0.0, 0.0),
            ),
            radar=RadarView(contacts=tuple(), locked_contact_id=None),
            wave=WaveView(
                label="No Wave",
                elapsed_s=0.0,
                duration_s=0.0,
                remaining_s=None,
                spawn_rate_per_min=0.0,
                friendly_prob=0.0,
                direction_bearing=0.0,
            ),
            mission=MissionView(
                mission_id="none",
                label="No Mission",
                description="",
                status="inactive",
                elapsed_s=0.0,
                time_left_s=None,
                decision=None,
            ),
            cap=CAPView(status="ready", sorties=0, time_in_status_s=0.0, harriers=tuple()),
            weapons=WeaponsView(slots=tuple()),
            audio=AudioView(events=tuple(), shots_in_flight=tuple()),
            weather=WeatherView(wind_dir_deg=0.0, wind_speed_kts=0.0, sea_state=0.0),
            radio=RadioView(messages=tuple()),
            nav_history=NavHistoryView(entries=tuple()),
            cap_history=CapHistoryView(entries=tuple()),
            health=HealthView(assets=tuple()),
        )

    def update_from_ship(self, ship: ShipState) -> None:
        self.update_ship(
            x_nm=ship.x_nm,
            y_nm=ship.y_nm,
            heading_deg=ship.heading_deg,
            speed_kts=ship.speed_kts,
        )

    def update_ship(
        self,
        *,
        x_nm: float,
        y_nm: float,
        heading_deg: float,
        speed_kts: float,
    ) -> None:
        cell = _grid_cell(x_nm, y_nm)
        hud = _hud_line(cell, heading_deg, speed_kts)
        with self._lock:
            self._snapshot = RuntimeSnapshot(
                ship=ShipView(
                    cell=cell,
                    x_nm=x_nm,
                    y_nm=y_nm,
                    heading_deg=heading_deg,
                    speed_kts=speed_kts,
                    hud=hud,
                ),
                radar=self._snapshot.radar,
                wave=self._snapshot.wave,
                mission=self._snapshot.mission,
                cap=self._snapshot.cap,
                weapons=self._snapshot.weapons,
                audio=self._snapshot.audio,
                weather=self._snapshot.weather,
                radio=self._snapshot.radio,
                nav_history=self._snapshot.nav_history,
                cap_history=self._snapshot.cap_history,
                health=self._snapshot.health,
            )

    def handle_ship_moved(self, event: ShipMoved) -> None:
        self.update_ship(
            x_nm=event.x_nm,
            y_nm=event.y_nm,
            heading_deg=event.heading_deg,
            speed_kts=event.speed_kts,
        )

    def update_radar(self, contacts: Sequence[RadarContactView]) -> None:
        as_tuple: Tuple[RadarContactView, ...] = tuple(contacts)
        with self._lock:
            if self._radar_locked_id is not None:
                if all(contact.id != self._radar_locked_id for contact in as_tuple):
                    self._radar_locked_id = None
            self._snapshot = RuntimeSnapshot(
                ship=self._snapshot.ship,
                radar=RadarView(contacts=as_tuple, locked_contact_id=self._radar_locked_id),
                wave=self._snapshot.wave,
                mission=self._snapshot.mission,
                cap=self._snapshot.cap,
                weapons=self._snapshot.weapons,
                audio=self._snapshot.audio,
                weather=self._snapshot.weather,
                radio=self._snapshot.radio,
                nav_history=self._snapshot.nav_history,
                cap_history=self._snapshot.cap_history,
                health=self._snapshot.health,
            )

    def handle_radar_contacts(self, event: RadarContactsUpdated) -> None:
        self.update_radar(event.contacts)

    def set_radar_lock(self, contact_id: Optional[int]) -> None:
        with self._lock:
            if contact_id is not None:
                if all(contact.id != contact_id for contact in self._snapshot.radar.contacts):
                    contact_id = None
            self._radar_locked_id = contact_id
            self._snapshot = RuntimeSnapshot(
                ship=self._snapshot.ship,
                radar=RadarView(
                    contacts=self._snapshot.radar.contacts,
                    locked_contact_id=self._radar_locked_id,
                ),
                wave=self._snapshot.wave,
                mission=self._snapshot.mission,
                cap=self._snapshot.cap,
                weapons=self._snapshot.weapons,
                audio=self._snapshot.audio,
                weather=self._snapshot.weather,
                radio=self._snapshot.radio,
                nav_history=self._snapshot.nav_history,
                cap_history=self._snapshot.cap_history,
                health=self._snapshot.health,
            )

    def update_mission(self, payload: Dict[str, object]) -> None:
        time_left_raw = payload.get("time_left_s")
        try:
            time_left = float(time_left_raw) if time_left_raw is not None else None
        except (TypeError, ValueError):
            time_left = None
        try:
            elapsed = float(payload.get("elapsed_s", 0.0))
        except (TypeError, ValueError):
            elapsed = 0.0
        mission = MissionView(
            mission_id=str(payload.get("id", "unknown")),
            label=str(payload.get("label", "")),
            description=str(payload.get("description", "")),
            status=str(payload.get("status", "inactive")),
            elapsed_s=elapsed,
            time_left_s=time_left,
            decision=payload.get("decision"),
        )
        with self._lock:
            self._snapshot = RuntimeSnapshot(
                ship=self._snapshot.ship,
                radar=self._snapshot.radar,
                wave=self._snapshot.wave,
                mission=mission,
                cap=self._snapshot.cap,
                weapons=self._snapshot.weapons,
                audio=self._snapshot.audio,
                weather=self._snapshot.weather,
                radio=self._snapshot.radio,
                nav_history=self._snapshot.nav_history,
                cap_history=self._snapshot.cap_history,
                health=self._snapshot.health,
            )

    def update_cap(self, snapshot: CAPSnapshot) -> None:
        harrier_views: Tuple[SeaHarrierView, ...] = tuple(
            SeaHarrierView(
                callsign=harrier.callsign,
                status=harrier.status.value if hasattr(harrier.status, "value") else str(harrier.status),
                fuel_pct=float(harrier.fuel_pct),
                time_in_status_s=float(harrier.time_in_status_s),
            )
            for harrier in getattr(snapshot, "harriers", tuple())
        )
        cap_view = CAPView(
            status=snapshot.status.value if hasattr(snapshot.status, "value") else str(snapshot.status),
            sorties=int(snapshot.sorties),
            time_in_status_s=float(snapshot.time_in_status_s),
            harriers=harrier_views,
        )
        with self._lock:
            self._snapshot = RuntimeSnapshot(
                ship=self._snapshot.ship,
                radar=self._snapshot.radar,
                wave=self._snapshot.wave,
                mission=self._snapshot.mission,
                cap=cap_view,
                weapons=self._snapshot.weapons,
                audio=self._snapshot.audio,
                weather=self._snapshot.weather,
                radio=self._snapshot.radio,
                nav_history=self._snapshot.nav_history,
                cap_history=self._snapshot.cap_history,
                health=self._snapshot.health,
            )

    def update_weapons(self, slots: Dict[str, WeaponSlot], cooldowns: Optional[Dict[str, float]] = None) -> None:
        cooldown_map = {name: max(0.0, float(value)) for name, value in (cooldowns or {}).items()}
        slot_views = tuple(
            WeaponSlotView(
                name=slot.name,
                state=str(slot.state),
                ammo=int(slot.ammo),
                max_ammo=int(slot.max_ammo),
                min_range_nm=float(slot.min_range_nm) if slot.min_range_nm is not None else None,
                max_range_nm=float(slot.max_range_nm) if slot.max_range_nm is not None else None,
                supports=tuple(slot.supports),
                ammo_per_shot=int(slot.ammo_per_shot),
                category=slot.category,
                cooldown_remaining_s=cooldown_map.get(slot.name, 0.0),
            )
            for _, slot in sorted(slots.items(), key=lambda item: item[0])
        )
        with self._lock:
            self._snapshot = RuntimeSnapshot(
                ship=self._snapshot.ship,
                radar=self._snapshot.radar,
                wave=self._snapshot.wave,
                mission=self._snapshot.mission,
                cap=self._snapshot.cap,
                weapons=WeaponsView(slots=slot_views),
                audio=self._snapshot.audio,
                weather=self._snapshot.weather,
                radio=self._snapshot.radio,
                nav_history=self._snapshot.nav_history,
                cap_history=self._snapshot.cap_history,
                health=self._snapshot.health,
            )

    def update_audio(self, events: Tuple[AudioEvent, ...], shots: Tuple[Dict[str, Any], ...]) -> None:
        serialised = tuple(
            {
                "kind": event.kind,
                "message": event.message,
                "ts": event.ts,
            }
            for event in events
        )
        serialised_shots = tuple(dict(shot) for shot in shots)
        with self._lock:
            self._snapshot = RuntimeSnapshot(
                ship=self._snapshot.ship,
                radar=self._snapshot.radar,
                wave=self._snapshot.wave,
                mission=self._snapshot.mission,
                cap=self._snapshot.cap,
                weapons=self._snapshot.weapons,
                audio=AudioView(events=serialised, shots_in_flight=serialised_shots),
                weather=self._snapshot.weather,
                radio=self._snapshot.radio,
                nav_history=self._snapshot.nav_history,
                cap_history=self._snapshot.cap_history,
                health=self._snapshot.health,
            )

    def update_weather(self, weather: WeatherState) -> None:
        view = WeatherView(
            wind_dir_deg=weather.wind_dir_deg,
            wind_speed_kts=weather.wind_speed_kts,
            sea_state=weather.sea_state,
        )
        with self._lock:
            self._snapshot = RuntimeSnapshot(
                ship=self._snapshot.ship,
                radar=self._snapshot.radar,
                wave=self._snapshot.wave,
                mission=self._snapshot.mission,
                cap=self._snapshot.cap,
                weapons=self._snapshot.weapons,
                audio=self._snapshot.audio,
                weather=view,
                radio=self._snapshot.radio,
                nav_history=self._snapshot.nav_history,
                cap_history=self._snapshot.cap_history,
                health=self._snapshot.health,
            )

    def update_radio(self, messages: Tuple[RadioMessage, ...]) -> None:
        serialised = tuple({
            "id": msg.id,
            "text": msg.text,
            "category": msg.category,
            "ts": msg.ts,
        } for msg in messages)
        with self._lock:
            self._snapshot = RuntimeSnapshot(
                ship=self._snapshot.ship,
                radar=self._snapshot.radar,
                wave=self._snapshot.wave,
                mission=self._snapshot.mission,
                cap=self._snapshot.cap,
                weapons=self._snapshot.weapons,
                audio=self._snapshot.audio,
                weather=self._snapshot.weather,
                radio=RadioView(messages=serialised),
                nav_history=self._snapshot.nav_history,
                cap_history=self._snapshot.cap_history,
                health=self._snapshot.health,
            )

    def update_nav_history(self, entries: Tuple[Dict[str, Any], ...]) -> None:
        with self._lock:
            self._snapshot = RuntimeSnapshot(
                ship=self._snapshot.ship,
                radar=self._snapshot.radar,
                wave=self._snapshot.wave,
                mission=self._snapshot.mission,
                cap=self._snapshot.cap,
                weapons=self._snapshot.weapons,
                audio=self._snapshot.audio,
                weather=self._snapshot.weather,
                radio=self._snapshot.radio,
                nav_history=NavHistoryView(entries=entries),
                cap_history=self._snapshot.cap_history,
                health=self._snapshot.health,
            )

    def update_cap_history(self, entries: Tuple[Dict[str, Any], ...]) -> None:
        with self._lock:
            self._snapshot = RuntimeSnapshot(
                ship=self._snapshot.ship,
                radar=self._snapshot.radar,
                wave=self._snapshot.wave,
                mission=self._snapshot.mission,
                cap=self._snapshot.cap,
                weapons=self._snapshot.weapons,
                audio=self._snapshot.audio,
                weather=self._snapshot.weather,
                radio=self._snapshot.radio,
                nav_history=self._snapshot.nav_history,
                cap_history=CapHistoryView(entries=entries),
                health=self._snapshot.health,
            )

    def update_wave(self, summary: Optional[Dict[str, Any]]) -> None:
        if summary is None:
            view = WaveView(
                label="No Wave",
                elapsed_s=0.0,
                duration_s=0.0,
                remaining_s=None,
                spawn_rate_per_min=0.0,
                friendly_prob=0.0,
                direction_bearing=0.0,
            )
        else:
            view = WaveView(
                label=str(summary.get("label", "Unknown")),
                elapsed_s=float(summary.get("elapsed_s", 0.0) or 0.0),
                duration_s=float(summary.get("duration_s", 0.0) or 0.0),
                remaining_s=(
                    float(summary["remaining_s"])
                    if summary.get("remaining_s") is not None
                    else None
                ),
                spawn_rate_per_min=float(summary.get("spawn_rate_per_min", 0.0) or 0.0),
                friendly_prob=float(summary.get("friendly_prob", 0.0) or 0.0),
                direction_bearing=float(summary.get("direction_bearing", 0.0) or 0.0),
            )
        with self._lock:
            self._snapshot = RuntimeSnapshot(
                ship=self._snapshot.ship,
                radar=self._snapshot.radar,
                wave=view,
                mission=self._snapshot.mission,
                cap=self._snapshot.cap,
                weapons=self._snapshot.weapons,
                audio=self._snapshot.audio,
                weather=self._snapshot.weather,
                radio=self._snapshot.radio,
                nav_history=self._snapshot.nav_history,
                cap_history=self._snapshot.cap_history,
                health=self._snapshot.health,
            )

    def update_health(self, assets: Tuple[AssetHealth, ...]) -> None:
        views = tuple(
            HealthAssetView(name=asset.name, max_lives=asset.max_lives, lives=asset.lives)
            for asset in assets
        )
        with self._lock:
            self._snapshot = RuntimeSnapshot(
                ship=self._snapshot.ship,
                radar=self._snapshot.radar,
                wave=self._snapshot.wave,
                mission=self._snapshot.mission,
                cap=self._snapshot.cap,
                weapons=self._snapshot.weapons,
                audio=self._snapshot.audio,
                weather=self._snapshot.weather,
                radio=self._snapshot.radio,
                nav_history=self._snapshot.nav_history,
                cap_history=self._snapshot.cap_history,
                health=HealthView(assets=views),
            )

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return self._snapshot

    def snapshot_dict(self) -> Dict[str, object]:
        return self.snapshot().as_dict()
