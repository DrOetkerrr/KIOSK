from __future__ import annotations

"""Runtime service wrapper for Falkland V2.

Provides a single object that owns the shared engine/cap/radar state that was
previously scattered across ``webdash`` globals. The first slice keeps behaviour
identical for Flask callers while giving us a stable surface that the upcoming
desktop shell can bind to.
"""

import os
import sys
import random
import threading
import time
import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from projects.falklands.core.engine import Engine
from projects.falklandV2.radar import Radar, WORLD_N
from projects.falklandV2.subsystems.hermes_cap import HermesCAP
from projects.falklandV2.subsystems import webcore as core
from projects.falklandV2.subsystems import ui_snapshot as ui_snap
from projects.falklandV2.subsystems.mission import MissionController
from projects.falklandV2.engine_adapter import contact_to_ui, _scale_legacy


class _RadarRecorder:
    """Mirror of webdash's recorder helper so radar logging stays untouched."""

    def __init__(self, runtime: "GameRuntime") -> None:
        self._runtime = runtime

    def log(self, event: str, data: Dict[str, Any] | None = None) -> None:  # pragma: no cover - resilience only
        runtime = self._runtime
        try:
            runtime.record_flight({
                "route": f"/radar/{event}",
                "method": "INT",
                "status": 200,
                "duration_ms": 0,
                "request": {},
                "response": {"event": event, **(data or {})},
            })
            if event == "ship.alarm.threat_close":
                cfg = runtime.load_alarm_cfg()
                auto = (cfg.get("auto") or {}).get("threat_close") or {}
                if bool(auto.get("enabled", False)):
                    rng = (data or {}).get("range_nm")
                    try:
                        thresh = float(auto.get("threshold_nm", 3.0))
                    except Exception:
                        thresh = 3.0
                    if not isinstance(rng, (int, float)) or float(rng) <= thresh:
                        msg_tpl = str(auto.get("message") or "Combat alarm! Threat inside {range_nm} nm.")
                        msg = msg_tpl.format(range_nm=(f"{float(rng):.1f}" if isinstance(rng, (int, float)) else "?"))
                        runtime.trigger_alarm(str(auto.get("sound") or "red-alert.wav"), message=msg,
                                              role=str(auto.get("role") or "Fire Control"), loop=False)
        except Exception:
            pass


class GameRuntime:
    """Holds the long-lived gameplay state used by both web and desktop fronts."""

    def __init__(self, *, port: Optional[int] = None, state_path: Optional[Path] = None) -> None:
        self.core = core
        self.port = port if port is not None else self._read_port()
        self.app_started = datetime.now(timezone.utc)
        self.state_lock = threading.Lock()
        self.audio_state = core.AUDIO_STATE
        self.record_flight = core.record_flight
        self.record_radio = core.record_radio
        self.trigger_alarm = core.trigger_alarm
        self.clear_alarm = core.clear_alarm
        self.stamp_cap_launch = core.stamp_cap_launch
        self.load_alarm_cfg = core.load_alarm_cfg
        self.log_dir = core.LOG_DIR
        self.flight_path = core.FLIGHT_PATH
        self.flight_max_bytes = core.FLIGHT_MAX_BYTES
        self.data_dir = core.DATA_DIR
        self.state_dir = core.STATE_DIR
        self.ammo_path = core.AMMO_PATH
        self.arming_path = core.ARMING_PATH
        self.weap_catalog_path = core.WEAP_CATALOG_PATH
        self.contacts_path = core.CONTACTS_PATH
        self.crew_path = core.CREW_PATH
        self.alarm_cfg_path = core.ALARM_CFG_PATH
        self.health_path = core.HEALTH_PATH
        self.tts_dir = core.TTS_DIR
        self.voice_events_path = core.VOICE_EVENTS_PATH
        self.skirmishes_path = core.SKIRMISHES_PATH
        self.roadmap_path = core.ROADMAP_PATH
        self.voices_dir = core.VOICES_DIR

        self._rebinder: Optional[Callable[["GameRuntime"], None]] = None

        self.state_path = state_path or Path.home() / "Documents" / "kiosk" / "falklands_state.json"
        self._engine_contacts: List[Any] = []
        self._engine_grid = SimpleNamespace(cols=float(WORLD_N), rows=float(WORLD_N), cell_nm=1.0)
        self._engine_state_cache: Dict[str, Any] = {}
        self.engine: Engine = self._create_engine()
        self.cap: Optional[HermesCAP] = self._create_cap()
        self._mission_settings_cache: Dict[str, Any] = {}
        self.radar: Radar = self._create_radar()
        self._install_engine_compat(self.engine)
        self._update_engine_state_view()
        now = time.time()
        self.mission: MissionController = MissionController(self.data_dir, now=now)
        self._last_engine_tick_ts: float = now
        self._last_radar_tick_ts: float = now
        self._mission_settings_cache = self.mission.current_settings()
        self._apply_mission_contact_filters(self.radar)

    # ---------- creation helpers
    def _read_port(self) -> int:
        try:
            return int(os.environ.get("PORT", "5055"))
        except Exception:
            return 5055

    def _create_engine(self) -> Engine:
        try:
            self.core.reset_damage_state()
        except Exception:
            pass
        return Engine(state_path=self.state_path)

    def _create_cap(self) -> Optional[HermesCAP]:
        try:
            return HermesCAP(self.data_dir)
        except Exception:
            return None

    def _create_radar(self) -> Radar:
        try:
            seed = os.environ.get("RADAR_SEED")
            rng = (random.Random(int(seed)) if seed is not None else random.Random())
        except Exception:
            rng = random.Random()
        radar = Radar(rec=_RadarRecorder(self), rng=rng, catalog_path=str(self.data_dir / "contacts.json"))
        try:
            radar.cap_effects_provider = (lambda: self.cap.current_effects() if self.cap is not None else {"active": False})
        except Exception:
            pass
        try:
            radar.cap_missions_provider = (lambda: (self.cap.snapshot().get('missions') if self.cap is not None else []))
        except Exception:
            pass
        # Bind CAP hooks: resolve target class/name and apply hit effects
        try:
            if self.cap is not None:
                self.cap.bind_target_resolver(lambda cid: next((c for c in radar.contacts if int(getattr(c, 'id', -1)) == int(cid)), None))  # type: ignore[attr-defined]
                def _cap_hit(cid: int, name: str, klass: str, ctx: Optional[Dict[str, Any]] = None) -> None:
                    try:
                        nm = str(name or '')
                        kl = str(klass or '')
                    except Exception:
                        nm = str(name)
                        kl = str(klass)
                    handled = False
                    sunk = False
                    try:
                        wd = sys.modules.get('projects.falklandV2.webdash')  # type: ignore
                        if wd is None:
                            from projects.falklandV2 import webdash as wd  # type: ignore
                    except Exception:
                        wd = None  # type: ignore
                    if wd is not None:
                        loadout = ''
                        weapon_label = None
                        if isinstance(ctx, dict):
                            loadout = str(ctx.get('loadout', '')).lower()
                            weapon_label = ctx.get('weapon')
                        if loadout == 'bombs':
                            weapon = str(weapon_label or 'Bomb')
                            with wd.STATE_LOCK:
                                try:
                                    handled, sunk = self.core.apply_enemy_ship_damage(wd, cid, weapon=weapon, target_name=nm, target_class=kl)
                                except Exception:
                                    handled = False
                    if handled:
                        if sunk:
                            return
                        return
                    # Fallback: remove contact immediately
                    try:
                        radar.contacts = [c for c in radar.contacts if int(getattr(c, 'id', -1)) != int(cid)]
                    except Exception:
                        pass
                self.cap.bind_hit_callback(_cap_hit)  # type: ignore[attr-defined]
        except Exception:
            pass
        seed_x = float(WORLD_N) / 2.0
        seed_y = float(WORLD_N) / 2.0
        try:
            ox, oy = self._own_xy()
            if isinstance(ox, (int, float)) and isinstance(oy, (int, float)):
                seed_x, seed_y = float(ox), float(oy)
        except Exception:
            pass
        try:
            if self.allow_hostile_contacts():
                radar.seed_test_contacts(seed_x, seed_y, count=4)
            else:
                bearings = (45.0, 315.0, 90.0)
                for bearing in bearings:
                    rng_nm = random.uniform(6.0, 12.0)
                    radar.force_spawn(seed_x, seed_y, 'Friendly', bearing, rng_nm)
        except Exception:
            pass
        self._apply_mission_contact_filters(radar)
        return radar

    # ---------- engine compatibility helpers ----------
    def _install_engine_compat(self, eng: Engine) -> None:
        if getattr(eng, '_ui_compat', False):
            return
        setattr(eng, '_ui_compat', True)

        # Shared contact store reused by pool/engine surfaces
        self._engine_contacts = []
        eng.contacts = self._engine_contacts

        # Provide pool/grid shim expected by ui_snapshot
        grid = self._engine_grid
        eng.pool = SimpleNamespace(contacts=self._engine_contacts, grid=grid)

        # Ensure hud() exists (ui snapshot calls eng.hud())
        if not hasattr(eng, 'hud'):
            eng.hud = eng.hud_line  # type: ignore[attr-defined]

        runtime = self

        def _ship_xy(_self) -> tuple[float, float]:  # pragma: no cover - thin shim
            pos = runtime._engine_state_cache.get('ship', {}).get('pos', {})
            try:
                return (float(pos.get('x', 0.0)), float(pos.get('y', 0.0)))
            except Exception:
                return (0.0, 0.0)

        def _ship_course_speed(_self) -> tuple[float, float]:  # pragma: no cover - thin shim
            ship = runtime._engine_state_cache.get('ship', {})
            try:
                return (float(ship.get('heading', 0.0)), float(ship.get('speed', 0.0)))
            except Exception:
                return (0.0, 0.0)

        eng._ship_xy = MethodType(_ship_xy, eng)     # type: ignore[attr-defined]
        eng._ship_course_speed = MethodType(_ship_course_speed, eng)  # type: ignore[attr-defined]

        # Provide mutable state dict expected by legacy surfaces
        runtime._update_engine_state_view()

    def _update_engine_state_view(self) -> None:
        try:
            st = getattr(self.engine, 'st', None)
            data = st.data if st is not None else {}
        except Exception:
            data = {}

        ship = data.get('ship', {}) if isinstance(data, dict) else {}
        pos = data.get('ship_position', {}) if isinstance(data, dict) else {}

        try:
            col_f = float(pos.get('col_f', ship.get('col', 50.0)))
        except Exception:
            col_f = 50.0
        try:
            row_f = float(pos.get('row_f', ship.get('row', 50.0)))
        except Exception:
            row_f = 50.0

        x = _scale_legacy(col_f)
        y = _scale_legacy(row_f)

        try:
            heading = float(ship.get('heading', data.get('ship_course_deg', 270.0)))
        except Exception:
            heading = 270.0
        try:
            speed = float(ship.get('speed', data.get('ship_speed_kn', 15.0)))
        except Exception:
            speed = 15.0

        self._engine_state_cache = {
            'ship': {
                'pos': {'x': x, 'y': y},
                'heading': heading,
                'speed': speed,
            },
            'radar': {
                'locked_contact_id': getattr(self.radar, 'priority_id', None)
            }
        }

        try:
            self.engine.state = self._engine_state_cache
        except Exception:
            pass

        # Refresh pool/grid with nominal WORLD_N metrics
        try:
            self._engine_grid.cols = float(WORLD_N)
            self._engine_grid.rows = float(WORLD_N)
            self._engine_grid.cell_nm = 1.0
        except Exception:
            pass

    # ---------- convenience surface ----------

    # ---------- lifecycle
    def register_rebinder(self, cb: Callable[["GameRuntime"], None]) -> None:
        self._rebinder = cb

    def _rebind(self) -> None:
        if self._rebinder is not None:
            try:
                self._rebinder(self)
            except Exception:
                pass

    def bind_mission_hooks(
        self,
        *,
        event_hook: Optional[Callable[[str, Optional[Dict[str, Any]]], None]] = None,
        voice_hook: Optional[Callable[..., None]] = None,
    ) -> None:
        try:
            if self.mission:
                self.mission.set_hooks(event_hook=event_hook, voice_hook=voice_hook)
        except Exception:
            pass

    def _mission_context(self, *, now: Optional[float] = None) -> Dict[str, Any]:
        ts = now if now is not None else time.time()
        try:
            health = self.core._load_health()
        except Exception:
            health = {}
        try:
            state = self.engine.public_state()
        except Exception:
            state = {}
        return {
            "now": ts,
            "health": health,
            "state": state,
        }

    def mission_snapshot(self) -> Dict[str, Any]:
        if not self.mission:
            return {}
        with self.state_lock:
            ctx = self._mission_context()
            snap = self.mission.update(ctx, now=ctx["now"])
            self._mission_settings_cache = self.mission.current_settings()
            return snap

    def apply_mission_decision(self, decision_id: str, choice: str) -> Dict[str, Any]:
        if not self.mission:
            return {"ok": False, "error": "mission_unavailable"}
        with self.state_lock:
            return self.mission.register_decision(decision_id, choice)

    def mission_settings(self) -> Dict[str, Any]:
        with self.state_lock:
            return dict(self._mission_settings_cache)

    def allow_hostile_contacts(self) -> bool:
        try:
            settings = getattr(self, '_mission_settings_cache', {}) or {}
            return bool(settings.get('hostile_spawns', True))
        except Exception:
            return True

    def activate_mission(self, mission_id: str) -> Dict[str, Any]:
        if not self.mission:
            return {"ok": False, "error": "mission_unavailable"}
        with self.state_lock:
            previous = dict(self._mission_settings_cache)
            res = self.mission.activate(mission_id, now=time.time())
            if res.get('ok'):
                self._mission_settings_cache = self.mission.current_settings()
                self._handle_mission_settings_change(previous, self._mission_settings_cache)
            return res

    def _handle_mission_settings_change(self, previous: Dict[str, Any], new: Dict[str, Any]) -> None:
        prev_hostiles = bool(previous.get('hostile_spawns', True))
        new_hostiles = bool(new.get('hostile_spawns', True))
        try:
            if prev_hostiles and not new_hostiles:
                self._apply_mission_contact_filters(self.radar)
            elif not prev_hostiles and new_hostiles:
                ox, oy = self._own_xy()
                try:
                    self.radar.seed_test_contacts(ox, oy, count=4)
                except Exception:
                    pass
        except Exception:
            pass
        self._apply_mission_contact_filters(self.radar)

    def reset_engine_and_cap(self) -> None:
        with self.state_lock:
            self.engine = self._create_engine()
            self.cap = self._create_cap()
            try:
                self.radar.cap_effects_provider = (lambda: self.cap.current_effects() if self.cap is not None else {"active": False})
            except Exception:
                pass
            now = time.time()
            self._last_engine_tick_ts = now
            self._last_radar_tick_ts = now
            self._sync_engine_contacts()
        self._rebind()

    def reset_state(self, *, clear_tts: bool = False) -> None:
        """Restore persistent state (ammo, arming, health, missions, etc.) to defaults.

        Also rebuilds engine/CAP/radar instances and clears cached audio state.
        """
        with self.state_lock:
            try:
                core.save_ammo(dict(core.WEAP_DEFAULT_AMMO))
            except Exception:
                pass
            try:
                core.save_arming(dict(core.WEAP_DEFAULT_ARMING))
            except Exception:
                pass
            try:
                core.reset_damage_state()
            except Exception:
                pass
            try:
                core._save_json(self.skirmishes_path, [])
            except Exception:
                pass
            try:
                core._save_json(self.roadmap_path, {})
            except Exception:
                pass
            try:
                core._save_json(self.state_dir / 'runtime.json', {})
            except Exception:
                pass
            if clear_tts:
                try:
                    for entry in self.tts_dir.iterdir():
                        if entry.is_file() or entry.is_symlink():
                            entry.unlink(missing_ok=True)
                        elif entry.is_dir():
                            shutil.rmtree(entry, ignore_errors=True)
                except Exception:
                    pass

            # Reset audio state cache
            core.AUDIO_STATE.clear()
            core.AUDIO_STATE.update({
                "last_launch": None,
                "last_result": None,
                "radio": None,
                "alarm": None,
                "cap_launch": None,
                "enemy_bomb": None,
                "shots_in_flight": [],
            })
            self.audio_state = core.AUDIO_STATE

            # Rebuild runtime components
            self.engine = self._create_engine()
            self.cap = self._create_cap()
            self.radar = self._create_radar()
            now = time.time()
            self.mission = MissionController(self.data_dir, now=now)
            self._last_radar_tick_ts = now
            self._sync_engine_contacts()
        self._rebind()

    # Convenience pass-throughs for callers that expect functions
    def load_ammo(self) -> Dict[str, Any]:
        return core.load_ammo()

    def save_ammo(self, obj: Dict[str, Any]) -> None:
        core.save_ammo(obj)

    def load_arming(self) -> Dict[str, Any]:
        return core.load_arming()

    def save_arming(self, obj: Dict[str, Any]) -> None:
        core.save_arming(obj)

    def compute_in_range(self, name: str, primary_ui: Optional[Dict[str, Any]]) -> Optional[bool]:
        return core.compute_in_range(name, primary_ui)

    # ---------- snapshots
    def build_ui_snapshot(self) -> Dict[str, Any]:
        """Direct, pure snapshot for desktop UI (no Flask import).
        Mirrors projects/falklandV2/subsystems/ui_snapshot.build_snapshot.
        """
        with self.state_lock:
            self._update_engine_state_view()
            contacts_ui, radar_meta = self._radar_snapshot()
            mission_block: Dict[str, Any] = {}
            try:
                mission_ctx = self._mission_context()
                mission_block = self.mission.update(mission_ctx, now=mission_ctx["now"]) if self.mission else {}
            except Exception:
                mission_block = {}
            try:
                paused = False
                convoy = None
                snap = ui_snap.build_snapshot(self.engine, self.cap, convoy, paused, self.data_dir)
                snap['contacts'] = contacts_ui
                radar_block = snap.get('radar') if isinstance(snap.get('radar'), dict) else {}
                if not isinstance(radar_block, dict):
                    radar_block = {}
                radar_block.update(radar_meta)
                snap['radar'] = radar_block
                if mission_block:
                    snap['mission'] = mission_block
                else:
                    snap['mission'] = snap.get('mission') or {}
                return snap
            except Exception:
                hud = "—"
                try:
                    hud = self.engine.hud_line()
                except Exception:
                    pass
                return {"hud": hud, "contacts": contacts_ui, "weapons": [], "cap": {}, "radar": radar_meta}

    # ---------- simple radar controls
    def _own_xy(self) -> tuple[float, float]:
        try:
            st = self.engine.public_state()
            return core.radar_xy_from_state(st)
        except Exception:
            return (0.0, 0.0)

    def _radar_snapshot(self) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        contacts_ui: List[Dict[str, Any]] = []
        radar_meta: Dict[str, Any] = {}
        try:
            now = time.time()
            self._advance_radar(now=now)
            own_xy = self._own_xy()
            contacts = list(getattr(self.radar, 'contacts', []))
            contacts_ui = [contact_to_ui(c, own_xy) for c in contacts]
            contacts_ui.sort(key=lambda d: float(d.get('range_nm', 1e9)))

            try:
                interval = int(getattr(self.radar, 'cfg', {}).get('scan_interval_s', 180))
            except Exception:
                interval = 180
            try:
                accum = float(getattr(self.radar, '_accum', 0.0))
            except Exception:
                accum = 0.0
            radar_meta['scan_interval_s'] = interval
            radar_meta['scan_left_s'] = max(0, int(round(interval - accum)))
            radar_meta['locked_contact_id'] = getattr(self.radar, 'priority_id', None)

            try:
                from projects.falklandV2.subsystems import radar as radar_sub

                class _Grid:
                    cell_nm = 1.0

                class _Pool:
                    def __init__(self, contacts):
                        self.contacts = contacts
                        self.grid = _Grid()

                radar_meta['status_line'] = radar_sub.status_line(
                    _Pool(contacts), own_xy,
                    locked_id=radar_meta['locked_contact_id'], max_list=3,
                )
            except Exception:
                pass
        except Exception:
            contacts_ui = []
        return contacts_ui, radar_meta

    def _apply_mission_contact_filters(self, radar: Optional[Radar]) -> None:
        if radar is None:
            return
        if self.allow_hostile_contacts():
            return
        try:
            filtered = []
            for contact in getattr(radar, 'contacts', []) or []:
                allegiance = str(getattr(contact, 'allegiance', '')).lower()
                if allegiance == 'hostile':
                    continue
                filtered.append(contact)
            radar.contacts = filtered
        except Exception:
            pass
        self._sync_engine_contacts()

    def _sync_engine_contacts(self) -> None:
        contacts = list(getattr(self.radar, 'contacts', []) or [])
        self._engine_contacts[:] = contacts
        try:
            self.engine.contacts = self._engine_contacts
        except Exception:
            pass
        try:
            if hasattr(self.engine, 'pool'):
                self.engine.pool.contacts = self._engine_contacts
        except Exception:
            pass
        self._update_engine_state_view()

    def _advance_engine(self, *, now: Optional[float] = None) -> None:
        if not hasattr(self, 'engine') or self.engine is None:
            return
        ts = now if now is not None else time.time()
        last = getattr(self, '_last_engine_tick_ts', None)
        if last is None:
            self._last_engine_tick_ts = ts
            return
        dt_total = max(0.0, float(ts) - float(last))
        if dt_total <= 0.0:
            return

        progressed = False
        remaining = dt_total
        while remaining > 0.0:
            step = min(1.0, remaining)
            with self.state_lock:
                try:
                    self.engine.tick(step)
                    progressed = True
                except Exception:
                    break
            remaining -= step

        if progressed:
            with self.state_lock:
                try:
                    if self.cap is not None:
                        self.cap.tick(now=ts)
                except Exception:
                    pass
                try:
                    self._update_engine_state_view()
                except Exception:
                    pass
        self._last_engine_tick_ts = ts

    def _advance_radar(self, *, now: Optional[float] = None) -> None:
        if not hasattr(self, 'radar') or self.radar is None:
            return
        try:
            ts = now if now is not None else time.time()
            last = getattr(self, '_last_radar_tick_ts', None)
            if last is None:
                self._last_radar_tick_ts = ts
                return
            dt_total = max(0.0, float(ts) - float(last))
            if dt_total <= 0.0:
                return
            self._advance_engine(now=ts)
            own_x, own_y = self._own_xy()
            # Advance in bounded steps so spawn math matches live loop expectations.
            remaining = dt_total
            while remaining > 0.0:
                step = min(1.0, remaining)
                try:
                    self.radar.tick(step, own_x, own_y)
                except Exception:
                    break
                remaining -= step
            self._last_radar_tick_ts = ts
            self._apply_mission_contact_filters(self.radar)
            self._sync_engine_contacts()
        except Exception:
            self._last_radar_tick_ts = now if now is not None else time.time()

    def radar_scan(self) -> Dict[str, Any]:
        ox, oy = self._own_xy()
        try:
            self.radar.scan(ox, oy)
            self._apply_mission_contact_filters(self.radar)
            self._sync_engine_contacts()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def radar_unlock(self) -> Dict[str, Any]:
        try:
            if hasattr(self.radar, 'clear_manual_lock'):
                self.radar.clear_manual_lock()
            else:
                self.radar.priority_id = None
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def radar_lock_nearest(self) -> Dict[str, Any]:
        try:
            ox, oy = self._own_xy()
            contacts = list(getattr(self.radar, 'contacts', []) or [])
            if not contacts:
                return {"ok": False, "error": "no_contacts"}
            nearest = None
            best = float('inf')
            for c in contacts:
                try:
                    dx = float(getattr(c, 'x', 0.0)) - float(ox)
                    dy = float(getattr(c, 'y', 0.0)) - float(oy)
                    d = dx*dx + dy*dy
                except Exception:
                    continue
                if d < best:
                    best = d
                    nearest = c
            if nearest is None:
                return {"ok": False, "error": "no_contacts"}
            tid = int(getattr(nearest, 'id', -1))
            if hasattr(self.radar, 'set_manual_lock'):
                self.radar.set_manual_lock(tid)
            else:
                self.radar.priority_id = tid
            return {"ok": True, "id": tid}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def radar_lock_by_id(self, cid: int) -> Dict[str, Any]:
        try:
            for c in self.radar.contacts:
                if int(getattr(c, "id", -1)) == int(cid):
                    if hasattr(self.radar, 'set_manual_lock'):
                        self.radar.set_manual_lock(int(cid))
                    else:
                        self.radar.priority_id = int(cid)
                    return {"ok": True, "id": int(cid)}
            return {"ok": False, "error": "not_found"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---------- weapons controls (parity with routes/weapons)
    def arm_weapon(self, name: str, state: str) -> Dict[str, Any]:
        if not name or state not in ("Armed", "Safe"):
            return {"ok": False, "error": "bad params"}
        try:
            raw = core._load_json(self.arming_path, {})
            if not isinstance(raw, dict):
                raw = {}
            if state == 'Armed':
                rec = {'armed': False, 'arming_until': time.time() + 5.0}
                disp_state = 'Arming'
            else:
                rec = {'armed': False, 'arming_until': 0}
                disp_state = 'Safe'
            raw[name] = rec
            core._save_json(self.arming_path, raw)
            # schedule arming ready via cooldown timestamp
            with self.state_lock:
                # frontends poll cooldown via status; we don't need a queue here
                pass
            return {"ok": True, "name": name, "state": disp_state}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _cooldown_left_s(self, nm: str) -> int:
        try:
            raw = core._load_json(self.arming_path, {})
            rec = (raw or {}).get(nm) if isinstance(raw, dict) else None
            if isinstance(rec, dict):
                cu = float(rec.get('cooldown_until', 0.0) or 0.0)
                left = int(max(0.0, cu - time.time()))
                return left
        except Exception:
            pass
        return 0

    def _set_cooldown_until(self, nm: str, until: float) -> None:
        try:
            raw = core._load_json(self.arming_path, {})
            if not isinstance(raw, dict):
                raw = {}
            rec = raw.get(nm)
            if not isinstance(rec, dict):
                rec = {'armed': False, 'arming_until': 0}
            rec['cooldown_until'] = float(until)
            raw[nm] = rec
            core._save_json(self.arming_path, raw)
        except Exception:
            pass

    def _cooldown_seconds_by_class(self, nm: str) -> float:
        try:
            wrec = next((w for w in core.WEAP_CATALOG if w.get('name') == nm), None)
            cls = (wrec or {}).get('class', 'Other')
            if (wrec or {}).get('cooldown_s') is not None:
                return float(wrec['cooldown_s'])
            if cls == 'Missile':
                return 8.0
            if cls == 'SAM':
                return 6.0
            if cls == 'Decoy':
                return 5.0
            return 2.0
        except Exception:
            return 3.0

    def fire_weapon(self, name: str, mode: str = 'real') -> Dict[str, Any]:
        mode = (mode or 'real').lower()
        if not name or mode not in ('real', 'test'):
            return {'ok': False, 'error': 'bad params'}
        ammo = self.load_ammo() or {}
        ammo.setdefault(name, 0)
        now = time.time()
        if self._cooldown_left_s(name) > 0:
            return {'ok': False, 'error': 'COOLDOWN'}
        arming_state = self.load_arming() or {}
        if arming_state.get(name) != 'Armed':
            return {'ok': False, 'error': 'NOT_ARMED'}
        if int(ammo.get(name, 0)) <= 0:
            return {'ok': False, 'error': 'NO_AMMO'}
        if mode == 'real':
            # compute primary and range gate
            primary = None
            try:
                st = self.engine.public_state()
                own = self._own_xy()
                pid = getattr(self.radar, 'priority_id', None)
                if pid is not None:
                    for c in self.radar.contacts:
                        if int(getattr(c, 'id', -1)) == int(pid):
                            primary = contact_to_ui(c, own)
                            break
            except Exception:
                primary = None
            if not primary:
                return {'ok': False, 'error': 'NO_PRIMARY'}
            if not self.compute_in_range(name, primary):
                return {'ok': False, 'error': 'OUT_OF_RANGE'}
        # consume ammo
        try:
            dec = 50 if name in ("20mm Oerlikon", "20mm GAM-BO1 (twin)") else 1
        except Exception:
            dec = 1
        ammo[name] = max(0, int(ammo.get(name, 0)) - int(dec))
        self.save_ammo(ammo)
        # audio cue
        try:
            with self.state_lock:
                self.audio_state['last_launch'] = {'weapon': core._sound_key_for_weapon(name), 'ts': time.time()}
        except Exception:
            pass
        # cooldown apply
        self._set_cooldown_until(name, now + self._cooldown_seconds_by_class(name))
        return {'ok': True, 'result': ('TEST' if mode=='test' else 'FIRE'), 'name': name, 'ammo': ammo[name]}


__all__ = ["GameRuntime"]
