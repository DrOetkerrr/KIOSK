from __future__ import annotations

"""Runtime service wrapper for Falkland V2.

Provides a single object that owns the shared engine/cap/radar state that was
previously scattered across ``webdash`` globals. The first slice keeps behaviour
identical for Flask callers while giving us a stable surface that the upcoming
desktop shell can bind to.
"""

import os
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from projects.falklands.core.engine import Engine
from projects.falklandV2.radar import Radar, WORLD_N
from projects.falklandV2.subsystems.hermes_cap import HermesCAP
from projects.falklandV2.subsystems import webcore as core
from projects.falklandV2.subsystems import ui_snapshot as ui_snap
from projects.falklandV2.engine_adapter import contact_to_ui


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
        self.engine: Engine = self._create_engine()
        self.cap: Optional[HermesCAP] = self._create_cap()
        self.radar: Radar = self._create_radar()

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
                def _cap_hit(cid: int, name: str, klass: str) -> None:
                    try:
                        nm = str(name or '')
                        kl = str(klass or '')
                    except Exception:
                        nm = str(name)
                        kl = str(klass)
                    # Belgrano special: 8 lives; sink at 0
                    if 'belgrano' in nm.lower():
                        try:
                            hl = self.core._load_health()
                            if 'belgrano_max_lives' not in hl:
                                hl['belgrano_max_lives'] = 8
                            if 'belgrano_lives' not in hl:
                                hl['belgrano_lives'] = hl.get('belgrano_max_lives', 8)
                            if int(hl.get('belgrano_lives', 0)) > 0:
                                hl['belgrano_lives'] = max(0, int(hl.get('belgrano_lives', 0)) - 1)
                                self.core._save_health(hl)
                            # Remove contact only when lives reach zero
                            if int(hl.get('belgrano_lives', 0)) <= 0:
                                radar.contacts = [c for c in radar.contacts if int(getattr(c, 'id', -1)) != int(cid)]
                        except Exception:
                            pass
                    else:
                        # Remove other targets immediately on hit
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
            radar.seed_test_contacts(seed_x, seed_y, count=4)
        except Exception:
            pass
        return radar

    # ---------- lifecycle
    def register_rebinder(self, cb: Callable[["GameRuntime"], None]) -> None:
        self._rebinder = cb

    def _rebind(self) -> None:
        if self._rebinder is not None:
            try:
                self._rebinder(self)
            except Exception:
                pass

    def reset_engine_and_cap(self) -> None:
        with self.state_lock:
            self.engine = self._create_engine()
            self.cap = self._create_cap()
            try:
                self.radar.cap_effects_provider = (lambda: self.cap.current_effects() if self.cap is not None else {"active": False})
            except Exception:
                pass
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
            contacts_ui, radar_meta = self._radar_snapshot()
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

    def radar_scan(self) -> Dict[str, Any]:
        ox, oy = self._own_xy()
        try:
            self.radar.scan(ox, oy)
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
