"""Runtime orchestration for Falkland V3."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, DefaultDict, Dict, Iterable, List, Optional, Tuple

import math
import random
from pathlib import Path
import time

from falklandv3.config import Settings, load_settings
from falklandv3.core.audio import AudioEvent, AudioQueue
from falklandv3.core.cap import CAPManager, CAPStatus
from falklandv3.core.engine import Engine
from falklandv3.core.events import RadarContactsUpdated, ShipMoved
from falklandv3.core.health import HealthManager
from falklandv3.core.mission import MissionManager, MissionLoader
from falklandv3.core.radar import RadarContact, RadarSimulator
from falklandv3.core.state import StateReducer
from falklandv3.core.weather import WeatherSimulator
from falklandv3.core.weapons import WeaponInventory, WeaponSlot
from falklandv3.core.radio import RadioFeed
from falklandv3.core.nav_history import NavHistory
from falklandv3.core.cap_history import CapLog
from falklandv3.data.catalog import ContactCatalog
from falklandv3.data.waves import load_wave_schedule
from falklandv3.services.persistence import PersistenceConfig, StateRepository
from falklandv3.utils.logging import log
from falklandv3.utils.grid import world_to_label

Subscriber = Callable[[object], None]

HOSTILE_STRIKE_RANGE_NM = 3.5


class EventBus:
    """Synchronous publish/subscribe bus used inside the runtime."""

    def __init__(self) -> None:
        self._subscribers: DefaultDict[str, List[Subscriber]] = defaultdict(list)
        self._lock = Lock()

    def subscribe(self, topic: str, callback: Subscriber) -> None:
        with self._lock:
            self._subscribers[topic].append(callback)

    def publish(self, topic: str, event: object) -> None:
        with self._lock:
            subscribers: Iterable[Subscriber] = list(self._subscribers.get(topic, ()))
        for callback in subscribers:
            callback(event)


@dataclass
class ShotInFlight:
    """Tracks a fired weapon awaiting resolution."""

    id: int
    weapon: str
    target: str
    cell: str
    range_nm: float
    pk_pct: int
    mode: str
    resolve_at: float
    result: Optional[str] = None
    linger_until: Optional[float] = None

    def eta_s(self, now: float) -> float:
        return max(0.0, self.resolve_at - now)

    def snapshot(self, now: float) -> Dict[str, Any]:
        return {
            "id": self.id,
            "weapon": self.weapon,
            "target": self.target,
            "cell": self.cell,
            "range_nm": self.range_nm,
            "pk_pct": self.pk_pct,
            "eta_s": self.eta_s(now),
            "result": self.result,
            "mode": self.mode,
        }


class GameRuntime:
    """Owns the core engine and coordinates domain services."""

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        repository: StateRepository | None = None,
        audio_max_events: int = 10,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.engine = Engine()
        self.events = event_bus or EventBus()
        package_dir = Path(__file__).resolve().parents[1]
        data_dir = package_dir / "data"
        root_data_dir = package_dir.parent / "data"
        catalog_path = data_dir / "contacts.json"
        if not catalog_path.exists():
            catalog_path = root_data_dir / "contacts.json"
        catalog = ContactCatalog(catalog_path) if catalog_path.exists() else None
        wave_schedule = None
        wave_path = data_dir / "attack_waves.json"
        if not wave_path.exists():
            wave_path = root_data_dir / "attack_waves.json"
        if wave_path.exists():
            wave_schedule = load_wave_schedule(wave_path)
        radar_rng = random.Random(self.settings.rng_seed) if self.settings.rng_seed is not None else None
        self.radar = RadarSimulator(catalog=catalog, wave_schedule=wave_schedule, rng=radar_rng)
        if self.settings.rng_seed is not None:
            weather_rng = random.Random(self.settings.rng_seed + 1)
            self.weather = WeatherSimulator(rng=weather_rng)
        else:
            self.weather = WeatherSimulator()
        health_path = data_dir / "health.json"
        if not health_path.exists():
            health_path = root_data_dir / "health.json"
        self.health = HealthManager.from_json(health_path) if health_path.exists() else HealthManager({})
        self.radio = RadioFeed()
        self.nav_history = NavHistory()
        self.cap_history = CapLog()
        self.state = StateReducer()
        self._weapons_rng = (
            random.Random(self.settings.rng_seed + 2)
            if self.settings.rng_seed is not None
            else random.Random()
        )
        self._shots_in_flight: List[ShotInFlight] = []
        self._next_shot_id = 1
        self._sim_time = 0.0
        self._radar_locked_id: Optional[int] = None
        self.state.update_from_ship(self.engine.ship)
        self.radar.ensure_seed_contacts(self.engine.ship.x_nm, self.engine.ship.y_nm)
        radar_views = tuple(self.radar.views(self.engine.ship.x_nm, self.engine.ship.y_nm))
        self.state.update_radar(radar_views)
        self.state.set_radar_lock(self._radar_locked_id)
        self.state.update_wave(self.radar.wave_summary())
        missions_path = Path(__file__).resolve().parents[1] / "data" / "missions"
        self.mission = MissionManager(MissionLoader(missions_path), health_provider=self.health.lives)
        self.state.update_mission(self.mission.snapshot())
        self.cap = CAPManager()
        self.state.update_cap(self.cap.snapshot())
        self.weapons = WeaponInventory()
        self.state.update_weapons(self.weapons.slots(), {})
        self.audio = AudioQueue(max_events=audio_max_events)
        self._sync_audio_state()
        self.state.update_weather(self.weather.snapshot())
        self.state.update_radio(tuple(self.radio.snapshot()))
        self.state.update_nav_history(self._nav_entries_payload())
        self.state.update_cap_history(self._cap_entries_payload())
        self._refresh_health_state()
        default_root = Path(__file__).resolve().parents[1] / "logs"
        self.repository = repository or StateRepository(PersistenceConfig(default_root))
        self._last_radio_id = 0
        self.events.subscribe("ship.moved", self.state.handle_ship_moved)
        self.events.subscribe("radar.contacts", self.state.handle_radar_contacts)
        self._lock = Lock()
        self._started_at = time.time()
        self._weapon_cooldowns: Dict[str, float] = {}
        self._persist_weather(self.weather.snapshot())
        self._persist_radio(tuple())

    def set_course(self, heading_deg: float) -> None:
        with self._lock:
            self.engine.set_course(heading_deg)
            self.state.update_from_ship(self.engine.ship)
            cmd = self.nav_history.record_course(heading_deg)
            self.state.update_nav_history(self._nav_entries_payload())
            self._persist_nav(cmd)
        log("nav.set_course", heading_deg=heading_deg)

    def set_speed(self, speed_kts: float) -> None:
        with self._lock:
            self.engine.set_speed(speed_kts)
            self.state.update_from_ship(self.engine.ship)
            cmd = self.nav_history.record_speed(speed_kts)
            self.state.update_nav_history(self._nav_entries_payload())
            self._persist_nav(cmd)
        log("nav.set_speed", speed_kts=speed_kts)

    def tick(self, dt_seconds: float) -> None:
        dt = max(0.0, float(dt_seconds))
        radar_views: tuple = tuple()
        pending_announces: List[Dict[str, object]] = []
        damage_log: List[str] = []
        weapon_slots_snapshot: Dict[str, WeaponSlot] = {}
        weapon_cooldowns_snapshot: Dict[str, float] = {}
        with self._lock:
            before = (self.engine.ship.x_nm, self.engine.ship.y_nm)
            self.engine.tick(dt)
            after = (self.engine.ship.x_nm, self.engine.ship.y_nm)
            moved = before != after
            ship_state = self.engine.ship
            self._sim_time += dt
            self.radar.tick(dt, ship_state.x_nm, ship_state.y_nm)
            radar_views = tuple(self.radar.views(ship_state.x_nm, ship_state.y_nm))
            wave_summary = self.radar.wave_summary()
            impact = self.radar.consume_hostile_within_range(
                ship_state.x_nm,
                ship_state.y_nm,
                HOSTILE_STRIKE_RANGE_NM,
            )
            if impact is not None:
                announce = self._apply_damage_locked("hermes", 1)
                if announce:
                    pending_announces.append(announce)
                info = self.health.asset("hermes")
                remaining = info.lives if info else 0
                max_lives = info.max_lives if info else 0
                message = (
                    f"Hermes hit by {impact.name}! {remaining}/{max_lives} lives remaining."
                    if info
                    else f"Hermes hit by {impact.name}!"
                )
                damage_log.append(message)
                radar_views = tuple(self.radar.views(ship_state.x_nm, ship_state.y_nm))
            self.mission.tick(dt)
            cap_before = self.cap.snapshot().status
            self.cap.tick(dt)
            cap_after_snapshot = self.cap.snapshot()
            weather_state = self.weather.tick(dt)
            self.radio.tick(dt)
            self.state.update_from_ship(ship_state)
            if self._radar_locked_id is not None and all(
                contact.id != self._radar_locked_id for contact in radar_views
            ):
                self._radar_locked_id = None
            self.state.update_radar(radar_views)
            self.state.set_radar_lock(self._radar_locked_id)
            self.state.update_wave(wave_summary)
            self.state.update_mission(self.mission.snapshot())
            self.state.update_cap(cap_after_snapshot)
            self.state.update_weather(weather_state)
            radio_messages = tuple(self.radio.snapshot())
            self.state.update_radio(radio_messages)
            self.state.update_nav_history(self._nav_entries_payload())
            announce = self.mission.consume_announce()
            if announce:
                pending_announces.append(announce)
            log("tick", dt=dt_seconds, moved=moved)
            self._refresh_health_state()
            weapon_slots_snapshot = self.weapons.slots()
            weapon_cooldowns_snapshot = self._weapon_cooldowns_snapshot_locked()
            self._update_shots()
            self._sync_audio_state()
        if cap_before != cap_after_snapshot.status and cap_after_snapshot.status == CAPStatus.RETURNING:
            intercepted = self._handle_cap_intercept(ship_state.x_nm, ship_state.y_nm)
            if intercepted:
                radar_views = tuple(self.radar.views(ship_state.x_nm, ship_state.y_nm))
                self.state.update_radar(radar_views)
                if self._radar_locked_id is not None and all(
                    contact.id != self._radar_locked_id for contact in radar_views
                ):
                    self._radar_locked_id = None
                self.state.set_radar_lock(self._radar_locked_id)
        if moved:
            self.events.publish(
                "ship.moved",
                ShipMoved(
                    heading_deg=ship_state.heading_deg,
                    speed_kts=ship_state.speed_kts,
                    x_nm=ship_state.x_nm,
                    y_nm=ship_state.y_nm,
                ),
            )
        self.events.publish(
            "radar.contacts",
            RadarContactsUpdated(contacts=radar_views),
        )
        for message in damage_log:
            self._log_damage_locked("hermes", message)
        for announce in pending_announces:
            self._handle_mission_announce(announce)
        self.state.update_weapons(weapon_slots_snapshot, weapon_cooldowns_snapshot)
        self._persist_snapshot()
        self._persist_weather(weather_state)
        self._persist_radio(radio_messages)

    def snapshot(self) -> Dict[str, object]:
        return self.state.snapshot_dict()

    def lock_radar_contact(self, contact_id: int) -> bool:
        with self._lock:
            if not any(contact.id == contact_id for contact in self.radar.contacts):
                return False
            self._radar_locked_id = contact_id
            self.state.set_radar_lock(contact_id)
        log("radar.lock", contact_id=contact_id)
        return True

    def unlock_radar_contact(self) -> None:
        with self._lock:
            self._radar_locked_id = None
            self.state.set_radar_lock(None)
        log("radar.unlock")

    def uptime_seconds(self) -> float:
        return time.time() - self._started_at

    def launch_cap(self) -> None:
        with self._lock:
            self.cap.launch()
            self.state.update_cap(self.cap.snapshot())
            self._push_audio_event("cap", f"CAP sortie {self.cap.snapshot().sorties} launched")
            entry = self.cap_history.record(
                "launch",
                sorties=self.cap.snapshot().sorties,
                mission_status=self._current_mission_status(),
            )
            self.state.update_cap_history(self._cap_entries_payload())
            self._persist_cap(entry)
        log("cap.launch")

    def reset_cap(self) -> None:
        with self._lock:
            self.cap = CAPManager()
            self.state.update_cap(self.cap.snapshot())
            self._push_audio_event("cap", "CAP state reset")
            entry = self.cap_history.record(
                "reset",
                sorties=self.cap.snapshot().sorties,
                mission_status=self._current_mission_status(),
            )
            self.state.update_cap_history(self._cap_entries_payload())
            self._persist_cap(entry)
        log("cap.reset")

    def arm_weapon(self, name: str) -> None:
        with self._lock:
            resolved = self.weapons.arm(name)
            cooldowns = self._weapon_cooldowns_snapshot_locked()
            self.state.update_weapons(self.weapons.slots(), cooldowns)
            display = resolved or name
            self._push_audio_event("weapon", f"{display} armed")
        log("weapon.arm", name=resolved or name)

    def safe_weapon(self, name: str) -> None:
        with self._lock:
            resolved = self.weapons.safe(name)
            cooldowns = self._weapon_cooldowns_snapshot_locked()
            self.state.update_weapons(self.weapons.slots(), cooldowns)
            display = resolved or name
            self._push_audio_event("weapon", f"{display} safed")
        log("weapon.safe", name=resolved or name)

    def fire_weapon(self, name: str, *, mode: str = "real") -> Dict[str, object]:
        mode_normalised = (mode or "real").lower()
        if mode_normalised not in {"real", "test"}:
            return {"ok": False, "error": "BAD_MODE"}
        with self._lock:
            slot = self.weapons._get_slot(name)  # internal use; returns live slot
            if slot is None:
                return {"ok": False, "error": "UNKNOWN_WEAPON"}
            canonical = slot.name
            current_time = self._sim_time
            cooldown_until = self._weapon_cooldowns.get(canonical, 0.0)
            if cooldown_until > current_time:
                return {"ok": False, "error": "COOLDOWN"}
            if not slot.armed:
                return {"ok": False, "error": "NOT_ARMED"}
            if slot.ammo <= 0:
                return {"ok": False, "error": "NO_AMMO"}

            target_info = self._select_primary_target()
            if mode_normalised == "real" and target_info is None:
                return {"ok": False, "error": "NO_TARGET"}
            contact: Optional[RadarContact]
            target_label = "—"
            distance = 0.0
            target_class: Optional[str] = None
            if target_info is not None:
                contact, target_class, distance = target_info
                target_label = contact.name
                if target_class and not self._weapon_supports_target(slot, target_class):
                    return {"ok": False, "error": "INVALID_TARGET"}
                if not self._weapon_in_range(slot, distance):
                    return {"ok": False, "error": "OUT_OF_RANGE"}
            else:
                contact = None

            if not self.weapons.fire(canonical):
                return {"ok": False, "error": "NO_AMMO"}
            self._weapon_cooldowns[canonical] = current_time + self._weapon_cooldown_seconds(slot)
            cooldowns = self._weapon_cooldowns_snapshot_locked(current_time)
            self.state.update_weapons(self.weapons.slots(), cooldowns)
            self._register_shot(slot, mode_normalised, contact, target_label, distance)
        self._push_audio_event("weapon", f"{canonical} {mode_normalised} fire")
        self.radio.push(f"{canonical} fired ({mode_normalised})", category="weapons")
        self.state.update_radio(tuple(self.radio.snapshot()))
        return {"ok": True, "result": mode_normalised.upper(), "name": canonical}

    def _push_audio_event(self, kind: str, message: str) -> None:
        event = AudioEvent(kind=kind, message=message, ts=time.time())
        self.audio.push(event)
        self._sync_audio_state()
        self._persist_audio(event)

    def _persist_snapshot(self) -> None:
        snapshot = self.state.snapshot_dict()
        try:
            self.repository.append_jsonl("snapshots.jsonl", [snapshot])
        except Exception:
            log("persist.snapshot.error", level="error")

    def _persist_audio(self, event: AudioEvent) -> None:
        try:
            self.repository.append_jsonl("audio_events.jsonl", [event.__dict__])
        except Exception:
            log("persist.audio.error", level="error")

    def _persist_weather(self, weather_state):
        payload = {
            "ts": time.time(),
            "wind_dir_deg": weather_state.wind_dir_deg,
            "wind_speed_kts": weather_state.wind_speed_kts,
            "sea_state": weather_state.sea_state,
        }
        try:
            self.repository.append_jsonl("weather.jsonl", [payload])
        except Exception:
            log("persist.weather.error", level="error")

    def _persist_radio(self, messages):
        new = [msg for msg in messages if msg.id > getattr(self, "_last_radio_id", 0)]
        if not new:
            return
        self._last_radio_id = new[-1].id
        serialised = [
            {"id": msg.id, "text": msg.text, "category": msg.category, "ts": msg.ts}
            for msg in new
        ]
        try:
            self.repository.append_jsonl("radio.jsonl", serialised)
        except Exception:
            log("persist.radio.error", level="error")

    def _persist_nav(self, command):
        payload = {"id": command.id, "ts": command.ts, "action": command.action, "value": command.value}
        try:
            self.repository.append_jsonl("nav_history.jsonl", [payload])
        except Exception:
            log("persist.nav.error", level="error")

    def _nav_entries_payload(self) -> tuple[Dict[str, Any], ...]:
        return tuple(
            {
                "id": entry.id,
                "ts": entry.ts,
                "action": entry.action,
                "value": entry.value,
            }
            for entry in self.nav_history.entries()
        )

    def _persist_cap(self, entry):
        payload = {
            "id": entry.id,
            "ts": entry.ts,
            "action": entry.action,
            "sorties": entry.sorties,
            "mission_status": entry.mission_status,
        }
        try:
            self.repository.append_jsonl("cap_history.jsonl", [payload])
        except Exception:
            log("persist.cap.error", level="error")

    def _cap_entries_payload(self) -> tuple[Dict[str, Any], ...]:
        return tuple(
            {
                "id": entry.id,
                "ts": entry.ts,
                "action": entry.action,
                "sorties": entry.sorties,
                "mission_status": entry.mission_status,
            }
            for entry in self.cap_history.entries()
        )

    def _shots_snapshot(self) -> Tuple[Dict[str, Any], ...]:
        now = self._sim_time
        shots = sorted(
            self._shots_in_flight,
            key=lambda shot: (
                shot.result is not None,
                shot.resolve_at if shot.result is None else (shot.linger_until or shot.resolve_at),
                shot.id,
            ),
        )
        return tuple(shot.snapshot(now) for shot in shots)

    def _sync_audio_state(self) -> None:
        self.state.update_audio(tuple(self.audio.events()), self._shots_snapshot())

    def _update_shots(self) -> None:
        now = self._sim_time
        updated: List[ShotInFlight] = []
        for shot in self._shots_in_flight:
            if shot.result is None and shot.resolve_at <= now:
                shot.result = self._resolve_shot_result(shot)
                linger = 6.0 if shot.mode == "real" else 4.0
                shot.linger_until = now + linger
            if shot.result is not None and shot.linger_until is not None and shot.linger_until <= now:
                continue
            updated.append(shot)
        self._shots_in_flight = updated

    def _estimate_time_to_impact(self, slot: WeaponSlot, distance_nm: float) -> float:
        category = (slot.category or "").lower()
        if "missile" in category or "sam" in category:
            return max(6.0, distance_nm / 0.35)
        if "gun" in category:
            return max(2.5, distance_nm / 0.6)
        if "ciws" in category or "20mm" in slot.name.lower():
            return max(1.5, distance_nm / 1.2)
        if "decoy" in category or "chaff" in slot.name.lower():
            return 2.0
        return max(3.0, distance_nm / 0.5)

    def _estimate_pk_pct(self, slot: WeaponSlot) -> int:
        category = (slot.category or "").lower()
        name = slot.name.lower()
        if "missile" in category or "sam" in category or "exocet" in name:
            return 85
        if "gun" in category:
            return 65
        if "ciws" in category or "20mm" in name:
            return 45
        if "decoy" in category or "chaff" in name:
            return 100
        return 50

    def _resolve_shot_result(self, shot: ShotInFlight) -> str:
        if shot.mode != "real":
            return shot.result or shot.mode.upper()
        if "chaff" in shot.weapon.lower() or "decoy" in shot.weapon.lower():
            return "DEPLOYED"
        probability = max(0.0, min(1.0, shot.pk_pct / 100))
        return "HIT" if self._weapons_rng.random() < probability else "MISS"

    def _register_shot(
        self,
        slot: WeaponSlot,
        mode: str,
        target_contact: Optional[RadarContact],
        target_label: str,
        distance_nm: float,
    ) -> None:
        now = self._sim_time
        weapon_display = slot.name
        cell = (
            world_to_label(target_contact.x_nm, target_contact.y_nm)
            if target_contact is not None
            else world_to_label(self.engine.ship.x_nm, self.engine.ship.y_nm)
        )
        pk_pct = self._estimate_pk_pct(slot)
        if mode != "real":
            result = "TEST" if mode == "test" else mode.upper()
            shot = ShotInFlight(
                id=self._next_shot_id,
                weapon=weapon_display,
                target=target_label,
                cell=cell,
                range_nm=distance_nm,
                pk_pct=pk_pct,
                mode=mode,
                resolve_at=now,
                result=result,
                linger_until=now + 4.0,
            )
        else:
            eta = self._estimate_time_to_impact(slot, distance_nm)
            shot = ShotInFlight(
                id=self._next_shot_id,
                weapon=weapon_display,
                target=target_label,
                cell=cell,
                range_nm=distance_nm,
                pk_pct=pk_pct,
                mode=mode,
                resolve_at=now + eta,
            )
        self._next_shot_id += 1
        self._shots_in_flight.append(shot)
        self._sync_audio_state()

    def _current_mission_status(self) -> str:
        snapshot = self.mission.snapshot()
        status = snapshot.get("status")
        return str(status or "unknown")

    def _handle_cap_intercept(self, ship_x: float, ship_y: float) -> bool:
        target = self.radar.intercept_hostile(ship_x, ship_y)
        if target is None:
            return False
        self.cap.record_intercept()
        self.state.update_cap(self.cap.snapshot())
        range_nm = math.hypot(target.x_nm - ship_x, target.y_nm - ship_y)
        weapon = (target.primary_weapon or "").lower()
        name = target.name
        if "exocet" in weapon or "exocet" in name.lower():
            radio_text = f"CAP splashed {name} before Exocet launch at {range_nm:.1f} NM"
            audio_text = f"Exocet threat neutralised by CAP"
        else:
            radio_text = f"CAP reports hostile {name} splashed at {range_nm:.1f} NM"
            audio_text = f"Hostile {name} intercepted"
        self.radio.push(radio_text, category="cap")
        self.state.update_radio(tuple(self.radio.snapshot()))
        self.mission.record_hostile_destroyed()
        self.state.update_mission(self.mission.snapshot())
        announce = self.mission.consume_announce()
        if announce:
            self._handle_mission_announce(announce)
        self._push_audio_event("cap", audio_text)
        log(
            "cap.intercept",
            contact_id=target.id,
            label=target.name,
            allegiance=target.allegiance,
        )
        return True

    def _handle_mission_announce(self, payload: Dict[str, object]) -> None:
        text = str(payload.get("voice_fallback") or payload.get("message") or "Mission update received.")
        category = "mission"
        self.radio.push(text, category=category)
        self.state.update_radio(tuple(self.radio.snapshot()))
        self._push_audio_event("mission", text)

    def _refresh_health_state(self) -> None:
        self.state.update_health(self.health.assets())

    def _select_primary_target(self) -> Optional[Tuple[RadarContact, str, float]]:
        ship = self.engine.ship
        hostiles = [contact for contact in self.radar.contacts if contact.allegiance.lower() == "hostile"]
        if not hostiles:
            return None
        closest = min(hostiles, key=lambda c: math.hypot(c.x_nm - ship.x_nm, c.y_nm - ship.y_nm))
        distance = math.hypot(closest.x_nm - ship.x_nm, closest.y_nm - ship.y_nm)
        category = (closest.category or "").lower()
        if "missile" in category or "exocet" in (closest.primary_weapon or "").lower():
            target_class = "Missile"
        elif category in {"aircraft", "helicopter"}:
            target_class = "Aircraft"
        elif category == "ship" or "ship" in (closest.primary_weapon or "").lower():
            target_class = "Ship"
        else:
            label = closest.name.lower()
            if any(word in label for word in ["mirage", "pucara", "harrier", "skyhawk", "bogey"]):
                target_class = "Aircraft"
            else:
                target_class = "Ship"
        return closest, target_class, distance

    def _weapon_supports_target(self, slot, target_class: str) -> bool:
        supports = {cls.lower() for cls in slot.supports}
        target = target_class.lower()
        if target == "missile":
            return "missile" in supports or "aircraft" in supports
        return target in supports

    def _weapon_in_range(self, slot, distance_nm: float) -> bool:
        if slot.min_range_nm is not None and distance_nm < slot.min_range_nm:
            return False
        if slot.max_range_nm is not None and distance_nm > slot.max_range_nm:
            return False
        return True

    def _weapon_cooldown_seconds(self, slot) -> float:
        category = slot.category.lower()
        if category == "missile":
            return 15.0
        if category == "sam":
            return 6.0
        if category in {"gun", "ciws"}:
            return 2.0
        if category == "decoy":
            return 10.0
        return 3.0

    def _weapon_cooldowns_snapshot_locked(self, now: Optional[float] = None) -> Dict[str, float]:
        current = self._sim_time if now is None else now
        expired = [name for name, expiry in self._weapon_cooldowns.items() if expiry <= current]
        for name in expired:
            self._weapon_cooldowns.pop(name, None)
        return {name: max(0.0, expiry - current) for name, expiry in self._weapon_cooldowns.items()}

    def damage_asset(self, asset: str, amount: int = 1) -> None:
        with self._lock:
            announce = self._apply_damage_locked(asset, amount)
            self._log_damage_locked(asset)
        if announce:
            self._handle_mission_announce(announce)

    def repair_asset(self, asset: str, amount: int = 1) -> None:
        with self._lock:
            announce = self._apply_repair_locked(asset, amount)
        if announce:
            self._handle_mission_announce(announce)

    def resolve_mission_decision(self, decision_id: str, choice: str) -> Dict[str, object]:
        with self._lock:
            announce = self.mission.resolve_decision(decision_id, choice)
            self.state.update_mission(self.mission.snapshot())
        if announce:
            self._handle_mission_announce(announce)
        return self.state.snapshot_dict()

    def _apply_damage_locked(self, asset: str, amount: int) -> Optional[Dict[str, object]]:
        if amount <= 0:
            return None
        self.health.damage(asset, amount)
        self._refresh_health_state()
        self.mission.recompute()
        self.state.update_mission(self.mission.snapshot())
        return self.mission.consume_announce()

    def _apply_repair_locked(self, asset: str, amount: int) -> Optional[Dict[str, object]]:
        if amount <= 0:
            return None
        self.health.repair(asset, amount)
        self._refresh_health_state()
        self.mission.recompute()
        self.state.update_mission(self.mission.snapshot())
        return self.mission.consume_announce()

    def _log_damage_locked(self, asset: str, message: Optional[str] = None) -> None:
        info = self.health.asset(asset)
        if info is not None and message is None:
            message = f"{asset.title()} damaged! {info.lives}/{info.max_lives} lives remaining."
        text = message or f"{asset.title()} took damage."
        self.radio.push(text, category="damage")
        self.state.update_radio(tuple(self.radio.snapshot()))
        self._push_audio_event("damage", text)
