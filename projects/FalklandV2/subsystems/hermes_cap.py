"""
Hermes CAP subsystem — behaves like an off-board "weapon with reach".
Now models Sea Harrier pairs with 2× Sidewinder missiles and simple range-based Pk.

Public surface:
- readiness(now=None) -> basic availability
- request_cap_to_cell(target_cell, *, distance_nm, now=None) -> launch a mission
- tick(now=None) -> advance mission states and recycle pairs
- auto_engage(distance_nm, locked_target_id, now=None) -> if on-station and in range, fire missiles
- snapshot(now=None) -> UI view
- current_effects() -> (unchanged placeholder hook for engine-side effects)
"""

from __future__ import annotations
import time, json, random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

def _read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t

def _interp(x: float, pts: List[Tuple[float, float]]) -> float:
    """Piecewise-linear interpolation of y over sorted (x,y) pts."""
    pts = sorted(pts, key=lambda p: p[0])
    if x <= pts[0][0]: return pts[0][1]
    if x >= pts[-1][0]: return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        if x0 <= x <= x1:
            t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
            return _lerp(y0, y1, t)
    return pts[-1][1]

_SURFACE_CLASS_HINTS = {
    'ship', 'surface', 'carrier', 'escort', 'merchant', 'convoy', 'landing',
    'frigate', 'destroyer', 'sub', 'submarine', 'boat', 'tanker', 'transport',
    'freighter', 'hms', 'ara', 'vessel'
}

def _is_surface_class(label: Any) -> bool:
    try:
        value = str(label or '').strip().lower()
    except Exception:
        value = ''
    if not value:
        return False
    return any(hint in value for hint in _SURFACE_CLASS_HINTS)


def _is_air_class(label: Any) -> bool:
    try:
        value = str(label or '').strip().lower()
    except Exception:
        value = ''
    if not value:
        return True
    return not _is_surface_class(value)

class CAPMission:
    """State machine: queued -> airborne -> onstation -> rtb -> recovering -> complete."""
    def __init__(self, mission_id: int, target_cell: str, cfg: Dict[str, Any], *, now: float, distance_nm: float,
                 onstation_min: Optional[float] = None, station_radius_nm: Optional[float] = None,
                 origin_xy: Optional[Tuple[float, float]] = None,
                 origin_cell: Optional[str] = None,
                 kind: str = "cap",
                 loadout: str = "aim9",
                 follow: Optional[str] = None,
                 intercept_speed_kts: Optional[float] = None,
                 intercept_target_kts: Optional[float] = None,
                 intercept_deck_cycle_s: Optional[int] = None):
        self.id = mission_id
        self.target_cell = target_cell
        self.distance_nm = float(distance_nm)
        self.status = "queued"
        self.ts: Dict[str, float] = {"created": now}
        self.cfg = cfg
        self.kind = kind if kind in ("cap", "intercept") else "cap"
        if isinstance(origin_xy, (tuple, list)) and len(origin_xy) == 2:
            self.origin_xy: Optional[Tuple[float, float]] = (float(origin_xy[0]), float(origin_xy[1]))
        else:
            self.origin_xy = None
        self.origin_cell: Optional[str] = str(origin_cell) if origin_cell else None
        # Optional dynamic follow mode (e.g., 'hermes')
        self.follow: Optional[str] = str(follow) if follow else None

        # Static params
        base_deck = int(cfg.get("deck_cycle_per_pair_s", 180))
        if self.kind == "intercept":
            self.deck_cycle_s = int(intercept_deck_cycle_s if intercept_deck_cycle_s is not None else max(15, base_deck // 6))
        else:
            self.deck_cycle_s = base_deck
        if self.kind == "intercept":
            self.onstation_s = int(onstation_min if onstation_min is not None else cfg.get("intercept_onstation_min", 2)) * 60
        else:
            self.onstation_s = int(onstation_min if onstation_min is not None else cfg.get("default_onstation_min", 20)) * 60
        self.bingo_rtb_buffer_s = int(cfg.get("bingo_rtb_buffer_min", 4)) * 60
        self.cruise_speed_kts = float(cfg.get("cruise_speed_kts", 420))
        if self.kind == "intercept":
            if intercept_speed_kts is not None:
                self.intercept_speed_kts = float(intercept_speed_kts)
            else:
                self.intercept_speed_kts = max(self.cruise_speed_kts, 540.0)
        else:
            self.intercept_speed_kts = self.cruise_speed_kts
        self.station_radius_nm = float(station_radius_nm if station_radius_nm is not None else cfg.get("station_radius_nm", 5))

        # Loadout: 'aim9' (Sidewinders) or 'bombs'
        self.loadout = 'bombs' if str(loadout).lower() in ('bomb', 'bombs') else 'aim9'
        weaps = (cfg.get("weapons") or {})
        sw = weaps.get("aim9", {})
        bombs = weaps.get("bombs", {})
        if self.loadout == 'bombs':
            self.missiles_total = int(bombs.get("bombs_total", bombs.get("missiles_total", 4)))
            self.engagement_cooldown_s = int(bombs.get("engagement_cooldown_s", 5))
        else:
            self.missiles_total = int(sw.get("missiles_total", 2))
            self.engagement_cooldown_s = int(sw.get("engagement_cooldown_s", 5))
        self.missiles_left = self.missiles_total
        self.last_engagement_s: float = 0.0
        self.last_engagement: Optional[Dict[str, Any]] = None
        self.permission_required: bool = True
        self.permission_authorized: bool = False
        self.permission_last_prompt_ts: float = 0.0
        self.permission_hold_since_ts: Optional[float] = None

        # Transit times from distance (one way)
        if self.kind == "intercept":
            dash_kts = float(self.intercept_speed_kts)
            tgt_kts = float(intercept_target_kts if intercept_target_kts is not None else 350.0)
            dash_nmps = max(dash_kts / 3600.0, 0.05)
            tgt_nmps = max(tgt_kts / 3600.0, 0.0)
            closure_nmps = max(dash_nmps + tgt_nmps, dash_nmps)
            one_leg_s = int(max(1.0, distance_nm / closure_nmps))
        else:
            one_leg_s = int((distance_nm / max(self.cruise_speed_kts, 1.0)) * 3600.0)
        self.outbound_s = max(1, one_leg_s)
        self.inbound_s = max(1, one_leg_s)

        # Derived timeline
        self.ts["launch"] = now
        self.ts["eta_onstation"] = now + self.deck_cycle_s + self.outbound_s
        self.ts["etd_rtb"] = None
        self.ts["eta_recovery"] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "n": self.id,
            "target_cell": self.target_cell,
            "cur_cell": self.target_cell,
            "kind": self.kind,
            "loadout": self.loadout,
            "follow": self.follow,
            "status": self.status,
            "distance_nm": self.distance_nm,
            "station_radius_nm": self.station_radius_nm,
            "deck_cycle_s": self.deck_cycle_s,
            "outbound_s": self.outbound_s,
            "inbound_s": self.inbound_s,
            "cruise_speed_kts": self.cruise_speed_kts,
            "intercept_speed_kts": self.intercept_speed_kts,
            "origin_xy": list(self.origin_xy) if self.origin_xy is not None else None,
            "origin_cell": self.origin_cell,
            "timestamps": self.ts,
            "missiles_left": self.missiles_left,
            "last_engagement": self.last_engagement,
            "permission": {
                "required": self.permission_required,
                "authorized": self.permission_authorized,
                "last_prompt_ts": self.permission_last_prompt_ts,
                "hold_since_ts": self.permission_hold_since_ts,
            }
        }

class HermesCAP:
    """Manages pool/cooldowns and missions; looks like a long-reach weapon to callers."""
    def __init__(self, data_path: Path, event_hook=None):
        self.data_path = data_path
        self.cfg = self._load_cfg()
        self.airframe_pool_total = int(self.cfg.get("airframe_pool_total", 8))
        self.airframe_pool_max = max(0, self.airframe_pool_total)
        self.ready_pairs_max = int(self.cfg.get("max_ready_pairs", 2))
        self.ready_pairs = self.ready_pairs_max
        self.cruise_speed_kts = float(self.cfg.get("cruise_speed_kts", 420))
        self.pair_rearm_refuel_s = int(self.cfg.get("pair_rearm_refuel_min", 25)) * 60
        self.scramble_cooldown_s = int(self.cfg.get("scramble_cooldown_min", 10)) * 60
        self.min_launch_interval_s = int(self.cfg.get("min_launch_interval_s", 30))
        self.last_scramble: float = 0.0
        self.missions: List[CAPMission] = []
        self._next_id = 1
        self._event_hook = event_hook
        self.max_pairs_on_task = int(self.cfg.get("max_pairs_on_task", 3))
        self.surge_pairs_on_task = int(self.cfg.get("surge_pairs_on_task", max(self.max_pairs_on_task, 4)))
        self.permission_timeout_s = int(self.cfg.get("permission_timeout_s", 600))
        self._permission_meta: Optional[Dict[int, Dict[str, Any]]] = None

        # Intercept tuning defaults
        self.intercept_speed_kts = float(self.cfg.get("intercept_speed_kts", 600))
        self.intercept_target_kts = float(self.cfg.get("intercept_target_kts", 350))
        base_deck = int(self.cfg.get("deck_cycle_per_pair_s", 180))
        self.intercept_deck_cycle_s = int(self.cfg.get("intercept_deck_cycle_s", max(15, base_deck // 6)))

        # Sidewinder engagement params (can be overridden by cap_config.json)
        wcfg = (self.cfg.get("weapons") or {}).get("aim9", {})
        self.sw_min_nm = float(wcfg.get("min_nm", 1.0))
        self.sw_max_nm = float(wcfg.get("max_nm", 5.0))
        # Pk control points (nm, probability)
        self.pk_pts: List[Tuple[float, float]] = wcfg.get("pk_points") or [
            (1.0, 0.30), (2.0, 0.55), (2.5, 0.65), (3.0, 0.55), (4.0, 0.35), (5.0, 0.20)
        ]
        # Runtime hooks
        self._target_resolver = None  # type: ignore
        self._hit_callback = None  # type: ignore
        self._voice_hook = None  # type: ignore
        # Optional target resolver and hit callback injected by runtime
        self._target_resolver = None  # type: ignore
        self._hit_callback = None  # type: ignore

    def _emit_event(self, event_id: str, data: Dict[str, Any] | None = None) -> None:
        if callable(self._event_hook):
            try:
                self._event_hook(event_id, data or {})
            except Exception:
                pass

    # ---------- runtime hooks for resolving targets and applying effects
    def bind_target_resolver(self, fn) -> None:
        self._target_resolver = fn

    def bind_hit_callback(self, fn) -> None:
        self._hit_callback = fn

    def bind_voice_hook(self, fn) -> None:
        self._voice_hook = fn

    def _pilot_call(self, event_id: str, data: Dict[str, Any] | None = None) -> None:
        if callable(self._voice_hook):
            try:
                self._voice_hook(event_id, data or {})
            except Exception:
                pass

    # ---------- permission/meta helpers
    def bind_permission_meta(self, meta: Dict[int, Dict[str, Any]]) -> None:
        self._permission_meta = meta
        for m in self.missions:
            self._ensure_meta_record(m.id)

    def _mission_by_id(self, mission_id: int) -> Optional[CAPMission]:
        for m in self.missions:
            if int(m.id) == int(mission_id):
                return m
        return None

    def _ensure_meta_record(self, mission_id: int) -> Optional[Dict[str, Any]]:
        if self._permission_meta is None:
            return None
        rec = self._permission_meta.setdefault(int(mission_id), {})
        rec.setdefault('asked', False)
        rec.setdefault('authorized', False)
        rec.setdefault('last_request_ts', 0.0)
        rec.setdefault('hold_since_ts', None)
        return rec

    def _drop_meta(self, mission_id: int) -> None:
        if self._permission_meta is None:
            return
        self._permission_meta.pop(int(mission_id), None)

    def set_permission(self, mission_id: int, authorized: bool, now: Optional[float] = None) -> None:
        mission = self._mission_by_id(mission_id)
        if mission is None:
            return
        t = now or time.time()
        mission.permission_authorized = bool(authorized)
        if authorized:
            mission.permission_hold_since_ts = None
            mission.permission_last_prompt_ts = t
        else:
            if mission.permission_hold_since_ts is None:
                mission.permission_hold_since_ts = t
        rec = self._ensure_meta_record(mission_id)
        if rec is not None:
            rec['authorized'] = bool(authorized)
            if authorized:
                rec['asked'] = False
                rec['hold_since_ts'] = None
                rec['last_auth_ts'] = t

    def mark_permission_prompted(self, mission_id: int, now: float) -> None:
        mission = self._mission_by_id(mission_id)
        if mission is None:
            return
        mission.permission_last_prompt_ts = now
        if mission.permission_hold_since_ts is None:
            mission.permission_hold_since_ts = now
        rec = self._ensure_meta_record(mission_id)
        if rec is not None:
            rec['asked'] = True
            rec['last_request_ts'] = now
            if rec.get('hold_since_ts') is None:
                rec['hold_since_ts'] = now

    def permission_state(self, mission_id: int) -> Optional[Dict[str, Any]]:
        mission = self._mission_by_id(mission_id)
        if mission is None:
            return None
        return {
            'required': mission.permission_required,
            'authorized': mission.permission_authorized,
            'last_prompt_ts': mission.permission_last_prompt_ts,
            'hold_since_ts': mission.permission_hold_since_ts,
        }

    def _transition_to_rtb(self, mission: CAPMission, now: float) -> None:
        if mission.status in ('rtb', 'recovering', 'complete'):
            return
        mission.status = 'rtb'
        mission.ts['rtb'] = now
        mission.ts['eta_recovery'] = now + mission.inbound_s

    def force_rtb(self, mission_id: int, *, reason: str | None = None, now: Optional[float] = None) -> None:
        mission = self._mission_by_id(mission_id)
        if mission is None:
            return
        t = now or time.time()
        self._transition_to_rtb(mission, t)
        mission.permission_authorized = False
        mission.permission_hold_since_ts = None
        rec = self._ensure_meta_record(mission_id)
        if rec is not None:
            rec['authorized'] = False
            rec['asked'] = False
            rec['hold_since_ts'] = None
        if reason:
            self._emit_event('cap.mission.rtb', {'mission_id': mission.id, 'reason': reason})
    # ---------- config
    def _load_cfg(self) -> Dict[str, Any]:
        f = self.data_path / "cap_config.json"
        if not f.exists():
            return {}
        try:
            return _read_json(f).get("cap_config", {})
        except Exception:
            return {}

    # ---------- weapon-like surface
    def readiness(self, now: Optional[float] = None) -> Dict[str, Any]:
        t = now or time.time()
        cd_left = max(0, int(self.scramble_cooldown_s - (t - self.last_scramble)))
        deck_left = max(0, int(getattr(self, 'min_launch_interval_s', 0) - (t - self.last_scramble)))
        return {
            "available": (self.ready_pairs >= 1 and self.airframe_pool_total >= 2 and cd_left == 0),
            "ready_pairs": self.ready_pairs,
            "airframes": self.airframe_pool_total,
            "cooldown_s": cd_left,
            "launch_interval_left_s": deck_left,
            "station_radius_nm": float(self.cfg.get("station_radius_nm", 5))
        }

    def request_cap_to_cell(self, target_cell: str, *, distance_nm: float, now: Optional[float] = None,
                            station_minutes: Optional[float] = None, radius_nm: Optional[float] = None,
                            origin_xy: Optional[Tuple[float, float]] = None,
                            origin_cell: Optional[str] = None,
                            mission_kind: str = "cap",
                            loadout: str = "aim9",
                            follow: Optional[str] = None) -> Dict[str, Any]:
        t = now or time.time()
        if (t - self.last_scramble) < self.min_launch_interval_s:
            return {"ok": False, "message": "Deck cycle in progress"}
        if (t - self.last_scramble) < self.scramble_cooldown_s:
            return {"ok": False, "message": "Scramble cooldown active"}
        if self.ready_pairs < 1:
            return {"ok": False, "message": "No ready pairs on deck"}
        if self.airframe_pool_total < 2:
            return {"ok": False, "message": "Insufficient airframes"}
        active_pairs = [m for m in self.missions if m.status in ('queued', 'airborne', 'onstation')]
        if len(active_pairs) >= self.surge_pairs_on_task:
            return {"ok": False, "message": "All CAP sorties committed"}
        if len(active_pairs) >= self.max_pairs_on_task and mission_kind != 'intercept':
            return {"ok": False, "message": "Max CAP stations active"}

        follow_norm = None
        if isinstance(follow, str):
            follow_norm = follow.strip().lower()
            if not follow_norm:
                follow_norm = None

        loadout_norm = 'bombs' if str(loadout).lower() in ('bomb', 'bombs') else 'aim9'
        if follow_norm == 'hermes' and loadout_norm != 'aim9':
            loadout_norm = 'aim9'

        m = CAPMission(self._next_id, target_cell, self.cfg, now=t, distance_nm=float(distance_nm),
                       onstation_min=(float(station_minutes) if station_minutes is not None else None),
                       station_radius_nm=(float(radius_nm) if radius_nm is not None else None),
                       origin_xy=origin_xy, origin_cell=origin_cell,
                       kind=mission_kind,
                       loadout=loadout_norm,
                       follow=follow_norm,
                       intercept_speed_kts=self.intercept_speed_kts if mission_kind == 'intercept' else None,
                       intercept_target_kts=self.intercept_target_kts if mission_kind == 'intercept' else None,
                       intercept_deck_cycle_s=self.intercept_deck_cycle_s if mission_kind == 'intercept' else None)
        self._next_id += 1
        self.missions.append(m)
        self.ready_pairs -= 1
        self.airframe_pool_total -= 2
        self.last_scramble = t
        self._ensure_meta_record(m.id)
        self.set_permission(m.id, False, now=t)
        # Promote immediately: eliminate deck wait between button press and launch
        m.deck_cycle_s = 0
        m.status = 'airborne'
        try:
            m.ts['eta_onstation'] = t + m.outbound_s
        except Exception:
            m.ts['eta_onstation'] = t + 1.0
        dest = target_cell if not follow_norm else f"follow:{follow_norm}"
        return {"ok": True, "message": f"Hermes: CAP pair launching to {dest}", "mission": m.to_dict()}

    def tick(self, now: Optional[float] = None) -> None:
        t = now or time.time()
        for m in self.missions:
            if m.status == "queued":
                if t >= m.ts["launch"] + m.deck_cycle_s:
                    m.status = "airborne"
            elif m.status == "airborne":
                if t >= m.ts["eta_onstation"]:
                    m.status = "onstation"
                    m.ts["onstation"] = t
                    m.ts["etd_rtb"] = t + m.onstation_s
                    self._emit_event('cap.onstation', {'mission_id': m.id, 'cell': m.target_cell})
            elif m.status == "onstation":
                if m.permission_required and not m.permission_authorized:
                    if m.permission_hold_since_ts is None:
                        m.permission_hold_since_ts = t
                    elif (t - m.permission_hold_since_ts) >= max(1, self.permission_timeout_s):
                        self._emit_event('cap.permission.timeout', {'mission_id': m.id, 'cell': m.target_cell})
                        self.force_rtb(m.id, reason='permission_timeout', now=t)
                        continue
                # Winchester: if out of missiles, RTB immediately
                try:
                    if int(getattr(m, 'missiles_left', 0) or 0) <= 0:
                        self._emit_event('cap.mission.rtb', {'mission_id': m.id, 'reason': 'winchester'})
                        self._transition_to_rtb(m, t)
                        continue
                except Exception:
                    pass
                if t >= (m.ts.get("etd_rtb") or t):
                    self._transition_to_rtb(m, t)
            elif m.status == "rtb":
                if t >= (m.ts.get("eta_recovery") or t):
                    m.status = "recovering"
                    m.ts["recovering"] = t
                    m.ts["ready_again"] = t + self.pair_rearm_refuel_s
            elif m.status == "recovering":
                if t >= (m.ts.get("ready_again") or t):
                    m.status = "complete"
                    m.ts["complete"] = t
                    self.ready_pairs = min(self.ready_pairs + 1, self.ready_pairs_max)
                    if self.airframe_pool_max:
                        self.airframe_pool_total = min(self.airframe_pool_total + 2, self.airframe_pool_max)
                    self._drop_meta(m.id)

        if len(self.missions) > 12:
            self.missions = [m for m in self.missions if m.status != "complete"][-12:]

    # ---------- engagement logic
    def _pk_for_range(self, range_nm: float) -> float:
        return 0.0 if (range_nm < self.sw_min_nm or range_nm > self.sw_max_nm) else float(_interp(range_nm, self.pk_pts))

    def auto_engage(self, distance_nm: Optional[float], locked_target_id: Optional[int], now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Engage using current mission loadout. Sidewinder vs air (2–5 nm). Bombs vs ships (≤1 nm)."""
        if distance_nm is None or locked_target_id is None:
            return None
        t = now or time.time()

        onst = [
            m for m in self.missions
            if m.status == "onstation"
            and m.missiles_left > 0
            and (not m.permission_required or m.permission_authorized)
        ]
        if not onst:
            return None
        m = onst[-1]

        if m.last_engagement_s and (t - m.last_engagement_s) < m.engagement_cooldown_s:
            return None

        # Resolve target class/name if possible
        target_class = None
        target_name = None
        if self._target_resolver and locked_target_id is not None:
            try:
                info = self._target_resolver(int(locked_target_id)) or {}
                target_name = str(getattr(info, 'name', getattr(info, 'Name', getattr(info, 'label', ''))))
                target_class = getattr(info, 'class', None) or getattr(info, 'type', None)
                if target_class is None:
                    meta = getattr(info, 'meta', {}) if hasattr(info, 'meta') else (info.get('meta') if isinstance(info, dict) else {})
                    cap = meta.get('cap') if isinstance(meta, dict) else {}
                    tclass = cap.get('class') if isinstance(cap, dict) else None
                    if tclass:
                        target_class = tclass
                if isinstance(target_class, str):
                    target_class = target_class.title()
            except Exception:
                target_class = None

        load = getattr(m, 'loadout', 'aim9')
        weapon_label = 'AIM-9' if load == 'aim9' else 'Bomb'
        target_label = target_name or getattr(m, 'target_cell', '') or 'Target'

        if load == 'aim9':
            if target_class and not _is_air_class(target_class):
                self._emit_event('cap.engage.denied', {
                    'mission_id': m.id,
                    'target_id': locked_target_id,
                    'reason': 'wrong_payload',
                    'loadout': load,
                    'target_class': target_class,
                })
                m.last_engagement_s = t
                return None
            if not (self.sw_min_nm <= float(distance_nm) <= self.sw_max_nm):
                return None
            pk = self._pk_for_range(float(distance_nm))
            hit1 = random.random() < pk
            m.missiles_left = max(0, m.missiles_left - 1)
            self._emit_event('cap.weapon.fire', {'mission_id': m.id, 'weapon': weapon_label, 'shot': 1, 'target_id': locked_target_id, 'range_nm': float(distance_nm)})
            self._pilot_call('pilot.fox2', {'target': target_label, 'mission_id': m.id, 'shot': 1})
            self._emit_event('cap.weapon.hit' if hit1 else 'cap.weapon.miss', {'mission_id': m.id, 'weapon': weapon_label, 'shot': 1, 'target_id': locked_target_id})
            if hit1 and callable(self._hit_callback):
                try:
                    ctx = {'mission_id': m.id, 'loadout': m.loadout, 'weapon': weapon_label}
                    self._hit_callback(int(locked_target_id), target_name or '', target_class or '', ctx)
                except Exception:
                    pass
            result = {"when": t, "target_id": int(locked_target_id), "range_nm": float(distance_nm), "pk": round(pk, 2), "shots": 1, "hit": hit1}
            if hit1:
                self._pilot_call('pilot.splash', {'target': target_label, 'mission_id': m.id})
            if (not hit1) and m.missiles_left > 0:
                hit2 = random.random() < pk
                m.missiles_left = max(0, m.missiles_left - 1)
                result.update({"shots": 2, "hit": hit2, "second_fired": True})
                self._emit_event('cap.weapon.fire', {'mission_id': m.id, 'weapon': weapon_label, 'shot': 2, 'target_id': locked_target_id, 'range_nm': float(distance_nm)})
                self._pilot_call('pilot.fox2', {'target': target_label, 'mission_id': m.id, 'shot': 2})
                self._emit_event('cap.weapon.hit' if hit2 else 'cap.weapon.miss', {'mission_id': m.id, 'weapon': weapon_label, 'shot': 2, 'target_id': locked_target_id})
                if hit2 and callable(self._hit_callback):
                    try:
                        ctx = {'mission_id': m.id, 'loadout': m.loadout, 'weapon': weapon_label}
                        self._hit_callback(int(locked_target_id), target_name or '', target_class or '', ctx)
                    except Exception:
                        pass
                if hit2:
                    self._pilot_call('pilot.splash', {'target': target_label, 'mission_id': m.id})
        else:
            # Bombs
            if target_class and not _is_surface_class(target_class):
                self._emit_event('cap.engage.denied', {
                    'mission_id': m.id,
                    'target_id': locked_target_id,
                    'reason': 'wrong_payload',
                    'loadout': load,
                    'target_class': target_class,
                })
                m.last_engagement_s = t
                return None
            if float(distance_nm) > 1.0:
                return None
            m.missiles_left = max(0, m.missiles_left - 1)
            self._emit_event('cap.weapon.fire', {'mission_id': m.id, 'weapon': weapon_label, 'shot': 1, 'target_id': locked_target_id, 'range_nm': float(distance_nm)})
            self._pilot_call('pilot.bombsaway', {'target': target_label, 'mission_id': m.id})
            self._emit_event('cap.weapon.hit', {'mission_id': m.id, 'weapon': weapon_label, 'shot': 1, 'target_id': locked_target_id})
            if callable(self._hit_callback):
                try:
                    ctx = {'mission_id': m.id, 'loadout': m.loadout, 'weapon': weapon_label}
                    self._hit_callback(int(locked_target_id), target_name or '', target_class or '', ctx)
                except Exception:
                    pass
            result = {"when": t, "target_id": int(locked_target_id), "range_nm": float(distance_nm), "pk": 1.0, "shots": 1, "hit": True}

        m.last_engagement = result
        m.last_engagement_s = t
        if load != 'aim9':
            event_id = 'pilot.target_hit' if result.get('hit') else 'pilot.target_miss'
            self._pilot_call(event_id, {'target': target_label, 'mission_id': m.id})
        try:
            if int(getattr(m, 'missiles_left', 0) or 0) <= 0:
                self._emit_event('cap.mission.rtb', {'mission_id': m.id, 'reason': 'winchester'})
                self._transition_to_rtb(m, t)
        except Exception:
            pass
        return result

    # ---------- effects surface for Engine (to hook into spawn/defence)
    def current_effects(self) -> Dict[str, Any]:
        eff = self.cfg.get("effects", {}) if self.cfg else {}
        onst = [m for m in self.missions if m.status == "onstation"]
        return {
            "active": len(onst) > 0,
            "stations": [
                {"target_cell": m.target_cell, "radius_nm": m.station_radius_nm, "effects": eff}
                for m in onst
            ]
        }

    # ---------- UI helpers
    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        r = self.readiness(now=now)
        t_now = now if now is not None else time.time()
        missions = []
        for m in self.missions:
            item = m.to_dict()
            eta_on = m.ts.get('eta_onstation') or 0.0
            etd_rtb = m.ts.get('etd_rtb') or 0.0
            eta_rec = m.ts.get('eta_recovery') or 0.0
            if m.status in ('queued', 'airborne'):
                item['tot_s'] = max(0, int(eta_on - t_now)) if eta_on else None
                item['tos_s'] = None
            elif m.status == 'onstation':
                item['tot_s'] = 0
                item['tos_s'] = max(0, int(etd_rtb - t_now)) if etd_rtb else None
            elif m.status == 'rtb':
                item['tot_s'] = None
                item['tos_s'] = max(0, int(eta_rec - t_now)) if eta_rec else None
            else:
                item['tot_s'] = None
                item['tos_s'] = None
            if m.origin_cell and 'origin_cell' not in item:
                item['origin_cell'] = m.origin_cell
            if 'range_nm' not in item:
                item['range_nm'] = item.get('distance_nm')
            missions.append(item)
        return {"readiness": r, "missions": missions}

    # ---------- retask helpers
    def convert_to_cap(self, mission_id: int, target_cell: str, *, minutes: Optional[float] = None, now: Optional[float] = None, follow: Optional[str] = None) -> Dict[str, Any]:
        """Retask an airborne/onstation mission to hold CAP at target_cell.

        Rules:
        - Only allowed for AIM-9 loadout and missiles_left > 0.
        - Sets status to 'onstation' immediately with a fresh on-station timer.
        - If minutes provided, overrides default onstation duration.
        """
        m = self._mission_by_id(mission_id)
        if m is None:
            return {"ok": False, "error": "mission not found"}
        if getattr(m, 'loadout', 'aim9') != 'aim9':
            return {"ok": False, "error": "wrong payload"}
        try:
            if int(getattr(m, 'missiles_left', 0) or 0) <= 0:
                return {"ok": False, "error": "winchester"}
        except Exception:
            pass
        t = now or time.time()
        try:
            m.target_cell = str(target_cell)
        except Exception:
            pass
        # Optional: set dynamic follow mode
        try:
            m.follow = str(follow) if follow else None
        except Exception:
            m.follow = None
        # Set CAP status/timers
        m.status = 'onstation'
        m.ts['onstation'] = t
        dur_s = int((float(minutes) * 60.0) if minutes is not None else self.cfg.get('default_onstation_min', 20) * 60)
        m.onstation_s = max(60, int(dur_s))
        m.ts['etd_rtb'] = t + m.onstation_s
        self._emit_event('cap.onstation', {'mission_id': m.id, 'cell': m.target_cell})
        # Reset permission to require authorization again
        try:
            self.set_permission(m.id, False, now=t)
        except Exception:
            pass
        return {"ok": True, "message": f"SHAR {m.id} holding CAP at {m.target_cell}", "mission": m.to_dict()}
