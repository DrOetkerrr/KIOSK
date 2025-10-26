#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Falklands V3 — Radar & Contacts (integrated module)

- 180s scan cadence (manual scan supported).
- No-spawn bubble (15–20 nm), with 1-in-6 “surprise” spawn at ~10–14 nm.
- Weighted hostile spawns (minimal built-in list; later load from rules).
- Contacts capped at 10; hostiles home (gently) toward own ship.
- Contact motion at 0.75× listed speed.
- Priority hostile selection (closest, then by weight).
- ship.alarm.threat_close when priority ≤ 3 nm (combat alarm).
"""

# Diff plan (RADAR Phase‑1):
# - Add Catalog class to load projects/falklandV2/data/contacts.json (hostiles/friendlies weighted pools).
# - Wire Catalog into Radar.__init__ (catalog_path, reload on init) and replace hostile pick in _spawn_attempt.
# - Add Radar.force_spawn(own_x, own_y, allegiance, bearing_deg, range_nm) to deterministically insert a contact.
# - Keep Contact dataclass, motion (tick), scan cadence, priority, and close-alarm logic unchanged.

from __future__ import annotations
import math, random, time, json, os, threading, re
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple, Callable, Set

try:
    from projects.falklandV2.subsystems.spawn_waves import WaveSchedule, WaveEnemy
except Exception:
    WaveSchedule = None  # type: ignore
    WaveEnemy = Any  # type: ignore

# --- World constants (match engine) ------------------------------------------
WORLD_N = 40
BOARD_N = 26

def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v

def nm_distance(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(bx - ax, by - ay)

# --- Minimal hostile table (subset; later move to rules) ---------------------
# Retained only for legacy threat flag logic; selection now uses Catalog
HOSTILES = [
    ("A-4 Skyhawk", 385, 5),
    ("Dagger (Mirage V)", 420, 4),
    ("Mirage III", 455, 3),
    ("Pucara", 196, 2),
    ("Super Etendard", 434, 1),
    ("Canberra bomber", 336, 1),
]
HOSTILE_SPEED_SCALE = 0.75  # move at 75% of real speed

# --- Catalog ---------------------------------------------------------------
class Catalog:
    def __init__(self, path: str | os.PathLike[str], rng: Optional[random.Random] = None):
        self.path = os.fspath(path)
        self.rng = rng or random.Random()
        self._hostile: List[Tuple[str, float, int, Optional[str]]] = []
        self._friendly: List[Tuple[str, float, int, Optional[str]]] = []
        # Details by name for capability lookups
        self._details: Dict[str, Dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        self._hostile.clear()
        self._friendly.clear()
        self._details.clear()
        try:
            txt = open(self.path, 'r', encoding='utf-8').read()
            data = json.loads(txt)
            items = data.get('items') if isinstance(data, dict) else data
            if not isinstance(items, list):
                return
            for it in items:
                if not isinstance(it, dict):
                    continue
                name = str(it.get('name', '')).strip()
                if not name:
                    continue
                allegiance = str(it.get('allegiance', '')).strip().title()
                try:
                    speed = float(it.get('speed_kts', 0.0))
                except Exception:
                    speed = 0.0
                try:
                    weight = int(it.get('weight', 1))
                except Exception:
                    weight = 1
                klass = (it.get('class') or it.get('type'))
                klass = str(klass) if klass is not None else None
                # Optional capability fields
                cap = {
                    'primary_weapon': it.get('primary_weapon'),
                    'min_range_nm': it.get('min_range_nm'),
                    'max_range_nm': it.get('max_range_nm'),
                    'class': klass,
                    'allegiance': allegiance,
                    'speed_kts': speed,
                }
                self._details[name] = cap
                if allegiance == 'Hostile':
                    self._hostile.append((name, speed, max(1, weight), klass))
                elif allegiance == 'Friendly':
                    # Policy: do not include Sea Harrier as a random friendly.
                    # Sea Harriers are represented via Hermes CAP only.
                    if name.lower() not in ('sea harrier frs.1', 'sea harrier', 'shar', 'sea harrier frs1'):
                        self._friendly.append((name, speed, max(1, weight), klass))
        except Exception:
            # Leave lists possibly empty; caller can handle
            pass

    def _pick_weighted(self, items: List[Tuple[str, float, int, Optional[str]]]) -> Tuple[str, float, Optional[str]]:
        if not items:
            return ("Contact", 0.0, None)
        total = float(sum(w for _n, _s, w, _k in items))
        r = self.rng.uniform(0.0, total)
        acc = 0.0
        for n, s, w, k in items:
            acc += w
            if r <= acc:
                return (n, float(s), k)
        n, s, _w, k = items[-1]
        return (n, float(s), k)

    def pick_hostile(self) -> Tuple[str, float, Optional[str]]:
        return self._pick_weighted(self._hostile)

    def pick_friendly(self) -> Tuple[str, float, Optional[str]]:
        return self._pick_weighted(self._friendly)

    def pick_hostile_by_name(self, name: str) -> Tuple[str, float, Optional[str]]:
        target = str(name)
        for n, s, _w, k in self._hostile:
            if n == target:
                return (n, float(s), k)
        return self.pick_hostile()

    def pick_hostile_weighted(self, mult_by_name: Optional[Dict[str, float]]) -> Tuple[str, float, Optional[str]]:
        """Pick a hostile applying name-based multipliers (0..1) to base weights.
        Falls back to base weights if map is empty/invalid.
        """
        items = self._hostile
        if not items or not mult_by_name:
            return self.pick_hostile()
        # Build adjusted list (name, speed, adj_weight, klass)
        adjusted: List[Tuple[str, float, int, Optional[str]]] = []
        for n, s, w, k in items:
            try:
                m = float(mult_by_name.get(n, 1.0))  # type: ignore[arg-type]
                if m < 0: m = 0.0
                if m > 1: m = 1.0
            except Exception:
                m = 1.0
            adj = max(0.0, float(w) * m)
            # Ensure at least a tiny weight to keep options open if all zero
            adjusted.append((n, s, max(adj, 0.0), k))
        # If all adjusted are zero, fall back
        if sum(int(x[2] > 0.0) for x in adjusted) == 0:
            return self.pick_hostile()
        total = sum(x[2] for x in adjusted)
        r = self.rng.uniform(0.0, float(total))
        acc = 0.0
        for n, s, w, k in adjusted:
            acc += w
            if r <= acc:
                return (n, float(s), k)
        n, s, _w, k = adjusted[-1]
        return (n, float(s), k)
    
    def details(self, name: str) -> Dict[str, Any]:
        return dict(self._details.get(name, {}))

    def counts(self) -> Tuple[int, int]:
        return (len(self._hostile), len(self._friendly))

# --- Contact model -----------------------------------------------------------
@dataclass
class Contact:
    id: int
    name: str
    allegiance: str   # "Hostile" | "Friendly" | "Neutral"
    x: float
    y: float
    course_deg: float
    speed_kts: float
    threat: str = "medium"
    last_warn_close: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)

    def tick(self, dt_s: float, own_x: float, own_y: float):
        # Guidance: hostiles gently steer towards own ship; missiles fly straight
        meta_obj = self.meta if isinstance(self.meta, dict) else {}
        retreat_heading = None
        if self.allegiance == "Hostile" and str(meta_obj.get('kind', '')).lower() != 'missile':
            intercept_xy: Optional[Tuple[float, float]] = None
            intercept_payload = meta_obj.get('r550_target')
            if isinstance(intercept_payload, dict):
                tx = intercept_payload.get('x')
                ty = intercept_payload.get('y')
                if isinstance(tx, (int, float)) and isinstance(ty, (int, float)):
                    fx = float(tx)
                    fy = float(ty)
                    if math.isfinite(fx) and math.isfinite(fy):
                        intercept_xy = (fx, fy)
            if intercept_xy is not None:
                target_x, target_y = intercept_xy
                desired = math.degrees(math.atan2(target_x - self.x, -(target_y - self.y))) % 360.0
                try:
                    rng_nm = nm_distance(self.x, self.y, target_x, target_y)
                except Exception:
                    rng_nm = None
                if rng_nm is not None:
                    if not math.isfinite(rng_nm):
                        rng_nm = None
                if rng_nm is not None:
                    try:
                        if isinstance(intercept_payload, dict):
                            prev = float(intercept_payload.get('range_nm', rng_nm))
                            if not math.isfinite(prev) or abs(prev - rng_nm) > 0.05:
                                intercept_payload['range_nm'] = float(rng_nm)
                                meta_obj['r550_target'] = intercept_payload
                    except Exception:
                        pass
            elif meta_obj.get('retreating'):
                try:
                    retreat_heading = float(meta_obj.get('retreat_heading', self.course_deg))
                except Exception:
                    retreat_heading = self.course_deg
                desired = retreat_heading
            else:
                # gentle steering toward own ship
                desired = math.degrees(math.atan2(own_x - self.x, -(own_y - self.y))) % 360.0
            turn = (desired - self.course_deg + 540) % 360 - 180
            max_turn_per_s = 5.0 / 60.0  # 5°/min
            turn_clamped = clamp(turn, -max_turn_per_s * dt_s, max_turn_per_s * dt_s)
            self.course_deg = (self.course_deg + turn_clamped) % 360.0
            if retreat_heading is not None:
                meta_obj['retreat_heading'] = retreat_heading
            if meta_obj is not self.meta:
                try:
                    self.meta = meta_obj
                except Exception:
                    pass

        if self.speed_kts > 0:
            nm = (self.speed_kts * HOSTILE_SPEED_SCALE) * (dt_s / 3600.0)
            rad = math.radians(self.course_deg)
            dx = math.sin(rad) * nm
            dy = -math.cos(rad) * nm
            next_x = self.x + dx
            next_y = self.y + dy
            if self.allegiance == "Hostile" and self.meta.get('retreating') and str(self.meta.get('kind', '')) != 'missile':
                # Allow retreating aircraft to depart the battlespace without being clamped back in.
                self.x = float(next_x)
                self.y = float(next_y)
            else:
                self.x = clamp(next_x, 0.0, float(WORLD_N))
                self.y = clamp(next_y, 0.0, float(WORLD_N))

# --- Radar -------------------------------------------------------------------
class Radar:
    def __init__(self, rec=None, cfg: Optional[dict] = None, rng: Optional[random.Random] = None, catalog_path: Optional[str] = None):
        self.rec = rec
        self.rng = rng or random.Random()
        self.cfg = {
            "scan_interval_s": 360,
            "no_spawn_nm": [15.0, 20.0],
            "surprise_nm": 10.0,
            "offboard_max_nm": 40.0,
            "max_contacts": 20,
            "close_threat_nm": 3.0,
            "close_alarm_cooldown_s": 30.0,
            # Probability a normal (non-surprise) spawn is Friendly instead of Hostile
            "friendly_prob": 0.2,
            "hermes_follow_radius_nm": 5.0,
            "hermes_follow_orbit_period_s": 240.0,
            # Time-based spawn rates (per minute), decoupled from scans
            # Roughly matches old behavior (~0.5 spawns per 3 minutes → ~0.166/min),
            # with ~1/3 of those being "surprise" spawns.
            "spawn_rate_per_min": 0.12,
            "surprise_rate_per_min": 0.02,
            # Force-spawn rate guard to avoid UI floods when callers misbehave.
            "force_spawn_rate_per_sec": 64,
            "force_spawn_burst_limit": 128,
            "force_spawn_window_s": 3.0,
        }
        if cfg: self.cfg.update(cfg)
        self.contacts: List[Contact] = []
        self._accum = 0.0
        self._pending_detection: Set[int] = set()
        self._next_id = 1
        self.priority_id: Optional[int] = None
        self._manual_lock = False
        self.wave_schedule: Optional[WaveSchedule] = None  # type: ignore[assignment]
        self._wave_elapsed = 0.0
        # Optional CAP effects provider (callable returning dict with keys: active, effects)
        self.cap_effects_provider: Optional[Callable[[], Dict[str, Any]]] = None
        # Optional CAP missions provider for placing friendly CAP flights on radar
        # Expected to return a list of dicts with keys: id, status, target_cell, origin_cell (optional)
        self.cap_missions_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None
        # Track injected CAP contacts by mission id -> contact ids keyed by role (leader/wingman)
        self._cap_contacts: Dict[int, Dict[str, int]] = {}
        # Optional resupply state provider (returns dict with keys: active, stage, origin_cell)
        self.resupply_state_provider: Optional[Callable[[], Dict[str, Any]]] = None
        self._resupply_contact_id: Optional[int] = None
        # Hostile formation tracking (pairs)
        self._enemy_formations: Dict[int, Dict[str, Any]] = {}
        self._enemy_formation_seq: int = 1
        self._enemy_spacing_cells: int = 2
        self._nm_per_cell: float = self._compute_nm_per_cell()
        # Catalog
        if catalog_path:
            self.catalog = Catalog(catalog_path, rng=self.rng)
        else:
            # best-effort default: relative to this file
            default_path = os.path.join(os.path.dirname(__file__), 'data', 'contacts.json')
            self.catalog = Catalog(default_path, rng=self.rng)
        self._lock = threading.RLock()
        mid_point = float(WORLD_N) / 2.0
        self._last_own_xy: Tuple[float, float] = (mid_point, mid_point)
        self._force_spawn_recent: deque[float] = deque()
        self._force_spawn_throttle_last_log = 0.0

    # API
    def tick(self, dt_s: float, own_x: float, own_y: float):
        with self._lock:
            ox, oy = self._origin_with_fallback(own_x, own_y)
            try:
                raw_ox = float(own_x)
                raw_oy = float(own_y)
            except Exception:
                raw_ox, raw_oy = ox, oy
            if math.isfinite(raw_ox) and math.isfinite(raw_oy):
                if abs(raw_ox) >= 1e-3 or abs(raw_oy) >= 1e-3:
                    self._last_own_xy = (raw_ox, raw_oy)
                else:
                    self._last_own_xy = (ox, oy)

            # cadence
            self._accum += dt_s
            try:
                self._wave_elapsed += max(0.0, float(dt_s))
            except Exception:
                self._wave_elapsed = 0.0
            if self._accum >= self.cfg["scan_interval_s"]:
                self._accum = 0.0
                self.scan(ox, oy)

            # time-based spawn chance (Poisson process per minute)
            try:
                # Determine current wave (if any) for spawn tuning
                wave = None
                if self.wave_schedule is not None:
                    try:
                        wave = self.wave_schedule.current(self._wave_elapsed)
                    except Exception:
                        wave = None
                # Clamp dt to sane bounds
                dt = max(0.0, min(float(dt_s), 5.0))
                # rates per second (default config)
                cfg_spawn_rate = float(self.cfg.get("spawn_rate_per_min", 0.1667))
                cfg_surprise_rate = float(self.cfg.get("surprise_rate_per_min", 0.0556))
                spawn_rate = cfg_spawn_rate
                surprise_rate = cfg_surprise_rate
                friendly_prob = float(self.cfg.get("friendly_prob", 0.3))
                if wave is not None:
                    try:
                        wr = getattr(wave, 'spawn_rate_per_min', None)
                        if wr is not None:
                            spawn_rate = max(0.0, float(wr))
                    except Exception:
                        pass
                    try:
                        sr = getattr(wave, 'surprise_rate_per_min', None)
                        if sr is not None:
                            surprise_rate = max(0.0, float(sr))
                    except Exception:
                        pass
                    try:
                        fp = getattr(wave, 'friendly_prob', None)
                        if fp is not None:
                            friendly_prob = max(0.0, min(1.0, float(fp)))
                    except Exception:
                        pass
                # rates per second
                import math as _m
                lam_norm = spawn_rate / 60.0
                lam_surp = surprise_rate / 60.0
                # spawn probability in dt window: 1 - exp(-lambda * dt)
                p_norm = 1.0 - _m.exp(-lam_norm * dt)
                p_surp = 1.0 - _m.exp(-lam_surp * dt)
                # First roll surprise (rare), else roll normal
                if self.rng.random() < p_surp:
                    self._spawn_attempt(ox, oy, wave=wave, friendly_prob=friendly_prob, surprise=True)
                elif self.rng.random() < p_norm:
                    self._spawn_attempt(ox, oy, wave=wave, friendly_prob=friendly_prob, surprise=False)
            except Exception:
                pass

            try:
                self._assign_r550_intercepts()
            except Exception:
                pass

            # motion
            for c in self.contacts:
                c.tick(dt_s, ox, oy)

            try:
                self._sync_hostile_formations(ox, oy)
            except Exception:
                pass

            # Inject or update CAP-friendly contacts so they appear on radar
            try:
                self._sync_cap_contacts()
            except Exception:
                pass
            # Inject/update Sea King when resupply active
            try:
                self._sync_resupply_contact()
            except Exception:
                pass

            # morale effects: some Argentine aircraft abort when Harriers are close
            try:
                self._apply_harrier_deterrence()
            except Exception:
                pass

            try:
                self._prune_retreating_contacts(ox, oy)
            except Exception:
                pass

            # priority + alarms
            self._select_priority(ox, oy)
            self._check_close_alarm(ox, oy)

            # cap count
            try:
                max_contacts = int(self.cfg.get("max_contacts", 10))
            except Exception:
                max_contacts = 10
            if len(self.contacts) > max_contacts:
                # Preserve CAP contacts; trim others but prioritise hostiles over friendlies
                caps = [c for c in self.contacts if bool(getattr(c, 'meta', {}).get('cap_flight'))]
                others = [c for c in self.contacts if not bool(getattr(c, 'meta', {}).get('cap_flight'))]
                hostiles = [c for c in others if str(getattr(c, 'allegiance', '')).lower() == 'hostile']
                non_hostiles = [c for c in others if str(getattr(c, 'allegiance', '')).lower() != 'hostile']
                ordered = hostiles + non_hostiles
                allow = max(0, max_contacts - len(caps))
                self.contacts = caps + ordered[:allow]

    def bind_wave_schedule(self, schedule: Optional[WaveSchedule]) -> None:  # type: ignore[override]
        self.wave_schedule = schedule
        if schedule is None:
            self._wave_elapsed = 0.0
        else:
            try:
                self._wave_elapsed = float(schedule.start_elapsed_s)
            except Exception:
                self._wave_elapsed = 0.0

    def scan(self, own_x: float, own_y: float):
        # Scans are observational and decoupled from spawns (spawns are time-based in tick)
        if self.rec:
            self.rec.log("radar.scan", {"interval_s": self.cfg["scan_interval_s"]})
        self._flush_pending_detections()

    def _flush_pending_detections(self) -> None:
        if not self._pending_detection:
            return
        for cid in list(self._pending_detection):
            contact = next((c for c in self.contacts if int(getattr(c, 'id', -1)) == int(cid)), None)
            if contact is None:
                self._pending_detection.discard(cid)
                continue
            if self.rec:
                cls = ''
                try:
                    cls = str(contact.meta.get('class')) if isinstance(contact.meta, dict) else ''
                except Exception:
                    cls = ''
                self.rec.log("radar.contact.new", {
                    "id": contact.id,
                    "name": contact.name,
                    "allegiance": contact.allegiance,
                    "class": cls,
                    "world_xy": [round(contact.x, 2), round(contact.y, 2)],
                    "course_deg": contact.course_deg,
                    "speed_kts": contact.speed_kts * HOSTILE_SPEED_SCALE,
                })
            self._pending_detection.discard(cid)

    def _origin_with_fallback(self, own_x: float, own_y: float) -> Tuple[float, float]:
        try:
            ox = float(own_x)
            oy = float(own_y)
        except Exception:
            return self._last_own_xy
        if not math.isfinite(ox) or not math.isfinite(oy):
            return self._last_own_xy
        if abs(ox) < 1e-3 and abs(oy) < 1e-3:
            return self._last_own_xy
        return (ox, oy)

    # internals
    def _spawn_attempt(
        self,
        own_x: float,
        own_y: float,
        *,
        wave: Optional[WaveDefinition] = None,
        friendly_prob: Optional[float] = None,
        surprise: bool = False,
    ):
        if len(self.contacts) >= self.cfg["max_contacts"]:
            if self.rec: self.rec.log("radar.spawn_skip", {"reason": "max_contacts"})
            return

        if surprise:
            base_min, base_max = self.cfg["surprise_nm"], 14.0
        else:
            r0, _r1 = self.cfg["no_spawn_nm"]
            base_min = float(r0)
            base_max = float(self.cfg.get("offboard_max_nm", 30.0))

        if wave is None and self.wave_schedule is not None:
            try:
                wave = self.wave_schedule.current(self._wave_elapsed)
            except Exception:
                wave = None

        wave_allows_hostiles = False
        if wave is not None:
            try:
                wave_allows_hostiles = bool(getattr(wave, "enemies", ()) or ())
            except Exception:
                wave_allows_hostiles = False

        def _sample_bearing() -> float:
            if self.wave_schedule is not None and wave is not None:
                try:
                    return self.wave_schedule.sample_bearing(wave, self.rng)
                except Exception:
                    pass
            return self.rng.uniform(0.0, 360.0)

        # Decide allegiance: surprise always Hostile; otherwise Friendly with configured probability
        if friendly_prob is None:
            try:
                friendly_prob = float(self.cfg.get("friendly_prob", 0.3))
            except Exception:
                friendly_prob = 0.3
        if surprise:
            allegiance = "Hostile"
        else:
            allegiance = ("Friendly" if (self.rng.random() < float(friendly_prob)) else "Hostile")

        wave_enemy = None
        if allegiance == "Hostile":
            if not wave_allows_hostiles:
                if self.rec:
                    self.rec.log("radar.spawn_skip", {"reason": "wave_suppressed"})
                return
        if allegiance == "Hostile" and wave is not None:
            enemies = getattr(wave, 'enemies', ())
            if not enemies:
                if self.rec: self.rec.log("radar.spawn_skip", {"reason": "wave_suppressed"})
                return
            try:
                wave_enemy = self.wave_schedule.pick_enemy(wave, self.rng)  # type: ignore[arg-type]
            except Exception:
                wave_enemy = None
            if wave_enemy is None:
                if self.rec: self.rec.log("radar.spawn_skip", {"reason": "wave_chance"})
                return

        enemy_min_nm: Optional[float] = None
        enemy_max_nm: Optional[float] = None

        # Pick from catalog based on allegiance (apply CAP spawn multipliers if provided and active)
        if allegiance == "Friendly":
            name, speed, klass = self.catalog.pick_friendly()
        else:
            mult_map: Optional[Dict[str, float]] = None
            try:
                if self.cap_effects_provider is not None:
                    eff = self.cap_effects_provider() or {}
                    if eff.get("active"):
                        emap = ((eff.get("stations") or [{}])[0]).get("effects", {})  # type: ignore[index]
                        mult_map = (emap.get("spawn_weight_multiplier") or None)
            except Exception:
                mult_map = None
            # Pick hostiles, respecting the Étendard exception for surprise spawns
            def _pick_hostile():
                return (self.catalog.pick_hostile_weighted(mult_map) if mult_map else self.catalog.pick_hostile())
            if wave_enemy is not None:
                name, speed, klass = self.catalog.pick_hostile_by_name(wave_enemy.name)
                enemy_min_nm = wave_enemy.min_range_nm
                enemy_max_nm = wave_enemy.max_range_nm
            else:
                name, speed, klass = _pick_hostile()
            if surprise:
                tries = 0
                while name == 'Super Etendard' and tries < 10:
                    name, speed, klass = _pick_hostile()
                    tries += 1

        min_nm = base_min
        max_nm = base_max
        if enemy_min_nm is not None:
            min_nm = max(min_nm, float(enemy_min_nm))
        if enemy_max_nm is not None:
            max_nm = min(max_nm, float(enemy_max_nm))
        if max_nm <= min_nm:
            max_nm = min_nm + 0.1

        friendly_cells_map: Dict[str, List[Contact]] = {}
        friendly_allow_overlap = False
        friendly_override_xy: Optional[Tuple[float, float]] = None
        if allegiance == "Friendly":
            friendly_radius_nm = max(self._nm_per_cell * 10.0, self._nm_per_cell)
            max_nm = min(max_nm, friendly_radius_nm)
            max_nm = max(max_nm, self._nm_per_cell * 0.5)
            min_nm = self._nm_per_cell * 0.25
            friendly_cells_map = self._occupied_cells()
            friendly_allow_overlap = str(klass or '').strip().lower() == 'aircraft'
            friendly_override_xy = self._friendly_spawn_xy(own_x, own_y, klass, friendly_cells_map)

        r = self.rng.uniform(min_nm, max_nm)

        bearing_deg = _sample_bearing()
        if allegiance == "Friendly" and friendly_override_xy is not None:
            dx_override = friendly_override_xy[0] - own_x
            dy_override = friendly_override_xy[1] - own_y
            r = math.hypot(dx_override, dy_override)
            if r > 0.0:
                bearing_deg = (math.degrees(math.atan2(dx_override, -dy_override)) % 360.0)
            else:
                bearing_deg = 0.0

        rad = math.radians(bearing_deg)
        dx = math.sin(rad) * r
        dy = -math.cos(rad) * r
        x = max(0.0, min(float(WORLD_N), own_x + dx))
        y = max(0.0, min(float(WORLD_N), own_y + dy))

        if allegiance == "Friendly" and friendly_override_xy is None:
            cell_label = self._cell_label_for_xy(x, y)
            if cell_label and friendly_cells_map.get(cell_label) and not friendly_allow_overlap:
                alt_xy = self._friendly_spawn_xy(own_x, own_y, klass, friendly_cells_map)
                if alt_xy is not None:
                    dx_alt = alt_xy[0] - own_x
                    dy_alt = alt_xy[1] - own_y
                    r = math.hypot(dx_alt, dy_alt)
                    if r > 0.0:
                        bearing_deg = (math.degrees(math.atan2(dx_alt, -dy_alt)) % 360.0)
                    else:
                        bearing_deg = 0.0
                    rad = math.radians(bearing_deg)
                    dx = math.sin(rad) * r
                    dy = -math.cos(rad) * r
                    x = max(0.0, min(float(WORLD_N), own_x + dx))
                    y = max(0.0, min(float(WORLD_N), own_y + dy))

        # Ensure Super Étendard starts at >= 20 nm (never uses 10 nm surprise)
        if allegiance == 'Hostile' and name == 'Super Etendard' and r < 20.0:
            r = max(20.0, r)
            dist = r
            bearing_deg = _sample_bearing()
            rad = math.radians(bearing_deg)
            dx = math.sin(rad) * dist
            dy = -math.cos(rad) * dist
            x = max(0.0, min(float(WORLD_N), own_x + dx))
            y = max(0.0, min(float(WORLD_N), own_y + dy))
        # Ensure hostile surface ships appear outside radar horizon (>= 20 nm)
        if allegiance == 'Hostile' and klass and str(klass).lower() == 'ship' and r < 20.0:
            r = max(20.0, self.cfg.get("surface_spawn_min_nm", 20.0))
            dist = r
            bearing_deg = _sample_bearing()
            rad = math.radians(bearing_deg)
            dx = math.sin(rad) * dist
            dy = -math.cos(rad) * dist
            x = max(0.0, min(float(WORLD_N), own_x + dx))
            y = max(0.0, min(float(WORLD_N), own_y + dy))

        course_deg = (bearing_deg + 180.0) % 360.0
        surface_meta: Optional[Dict[str, Any]] = None
        if str(allegiance).lower() == 'hostile' and str(klass or '').lower() == 'ship':
            surface_meta = {'hp': 4.0, 'max_hp': 4.0}
        meta = {
            "spawn": {"bearing_deg": round(bearing_deg,1), "range_nm": round(r,2), "surprise": surprise, "allegiance": allegiance},
            "cap": self.catalog.details(name),
            "class": klass,
        }
        if surface_meta is not None:
            meta['surface_ship'] = surface_meta
        c = Contact(
            id=self._next_id, name=name, allegiance=allegiance,
            x=x, y=y, course_deg=course_deg, speed_kts=float(speed),
            threat=("medium" if allegiance == "Hostile" else "low"),
            meta=meta
        )
        self._next_id += 1
        # CAP pre-release intercept chance: if active and mapping provides type-specific chance
        try:
            if self.cap_effects_provider is not None:
                eff = self.cap_effects_provider() or {}
                if eff.get("active"):
                    emap = ((eff.get("stations") or [{}])[0]).get("effects", {})  # type: ignore[index]
                    ipr = (emap.get("intercept_prob_pre_release") or {})
                    p = float(ipr.get(name, 0.0)) if name in ipr else 0.0
                    if p > 0.0 and self.rng.random() < max(0.0, min(1.0, p)):
                        if self.rec:
                            try:
                                self.rec.log("cap.intercept_pre_release", {
                                    "name": name, "range_nm": round(r, 2), "bearing_deg": round(bearing_deg, 1)
                                })
                            except Exception:
                                pass
                        return  # intercepted; do not add
        except Exception:
            pass

        self.contacts.append(c)

        if self.rec:
            self.rec.log("radar.spawn_attempt", {
                "bearing_deg": round(bearing_deg, 1),
                "range_nm": round(r, 2),
                "surprise": surprise,
                "chosen": {"name": name, "speed_kts": speed, "allegiance": allegiance},
                "target_world_xy": [round(x, 2), round(y, 2)],
                "ship_world_xy": [round(own_x, 2), round(own_y, 2)],
                "policy": {
                    "no_spawn_nm": self.cfg["no_spawn_nm"],
                    "surprise_nm": self.cfg["surprise_nm"],
                    "offboard_max_nm": self.cfg["offboard_max_nm"],
                    "max_contacts": self.cfg["max_contacts"],
                    "friendly_prob": self.cfg.get("friendly_prob", 0.3),
                }
            })
        if str(allegiance).lower() == 'hostile':
            self._pending_detection.add(c.id)
            self._maybe_spawn_hostile_wingman(c, klass=klass, name=name, own_x=own_x, own_y=own_y)
        else:
            if self.rec:
                cls = str(klass)
                self.rec.log("radar.contact.new", {
                    "id": c.id,
                    "name": c.name,
                    "allegiance": c.allegiance,
                    "class": cls,
                    "world_xy": [round(c.x, 2), round(c.y, 2)],
                    "course_deg": c.course_deg,
                    "speed_kts": c.speed_kts * HOSTILE_SPEED_SCALE,
                })

    def _maybe_spawn_hostile_wingman(self, leader: Contact, *, klass: Optional[str], name: str, own_x: float, own_y: float) -> None:
        if len(self.contacts) >= int(self.cfg.get("max_contacts", 20) or 20):
            return
        if leader.allegiance != "Hostile":
            return
        klass_norm = str(klass or '').strip().lower()
        if klass_norm != 'aircraft':
            return
        if str(name).strip() == 'Super Étendard':
            return
        spacing_nm = self._enemy_spacing_nm()
        formation_id = self._enemy_formation_seq
        self._enemy_formation_seq += 1
        rad = math.radians(leader.course_deg)
        ux = math.sin(rad)
        uy = -math.cos(rad)
        if abs(ux) < 1e-6 and abs(uy) < 1e-6:
            dx = float(own_x) - float(leader.x)
            dy = float(own_y) - float(leader.y)
            norm = math.hypot(dx, dy)
            if norm > 1e-6:
                ux, uy = dx / norm, dy / norm
            else:
                ux, uy = 0.0, -1.0
        target_x, target_y = self._formation_wingman_position(float(leader.x), float(leader.y), spacing_nm, ux, uy)
        wing_meta = {
            "spawn": dict(leader.meta.get('spawn', {})),
            "cap": dict(leader.meta.get('cap', {})),
            "class": klass,
            "formation": {
                "id": formation_id,
                "role": "wingman",
                "leader_id": leader.id,
                "spacing_cells": self._enemy_spacing_cells,
            },
        }
        wing_meta["spawn"]["wingman"] = True
        wingman = Contact(
            id=self._next_id,
            name=leader.name,
            allegiance=leader.allegiance,
            x=target_x,
            y=target_y,
            course_deg=leader.course_deg,
            speed_kts=leader.speed_kts,
            threat=leader.threat,
            meta=wing_meta,
        )
        self._next_id += 1
        self.contacts.append(wingman)
        self._pending_detection.add(wingman.id)
        leader.meta.setdefault('formation', {})
        leader.meta['formation'].update({
            "id": formation_id,
            "role": "leader",
            "wingman_id": wingman.id,
            "spacing_cells": self._enemy_spacing_cells,
        })
        self._enemy_formations[formation_id] = {
            "leader_id": leader.id,
            "wingman_id": wingman.id,
            "spacing_nm": spacing_nm,
        }

    def _is_r550_interceptor(self, contact: Contact) -> bool:
        try:
            if str(getattr(contact, 'allegiance', '')).lower() != 'hostile':
                return False
            name = str(getattr(contact, 'name', '')).strip()
            if name == 'Mirage III (R550)':
                return True
            meta = getattr(contact, 'meta', {}) or {}
            if isinstance(meta, dict):
                primary = meta.get('primary_weapon')
                if not primary and isinstance(meta.get('cap'), dict):
                    primary = meta['cap'].get('primary_weapon')
                if isinstance(primary, str) and 'r550' in primary.lower():
                    return True
        except Exception:
            return False
        return False

    def _is_shar_contact(self, contact: Contact) -> bool:
        try:
            if str(getattr(contact, 'allegiance', '')).lower() != 'friendly':
                return False
            name = str(getattr(contact, 'name', '')).lower()
            if 'sea harrier' in name or name == 'shar':
                return True
            meta = getattr(contact, 'meta', {}) or {}
            if isinstance(meta, dict):
                if meta.get('cap_flight'):
                    disp = str(meta.get('display_name') or meta.get('callsign') or name).lower()
                    if 'harrier' in disp or 'shar' in disp:
                        return True
        except Exception:
            return False
        return False

    @staticmethod
    def _finite_xy(x: Any, y: Any) -> Optional[Tuple[float, float]]:
        try:
            fx = float(x)
            fy = float(y)
        except Exception:
            return None
        if not (math.isfinite(fx) and math.isfinite(fy)):
            return None
        return (fx, fy)

    def _assign_r550_intercepts(self) -> None:
        if not self.contacts:
            return
        shar_contacts = [c for c in self.contacts if self._is_shar_contact(c)]
        if not shar_contacts:
            for hostile in self.contacts:
                if not self._is_r550_interceptor(hostile):
                    continue
                meta_obj = getattr(hostile, 'meta', {}) or {}
                if not isinstance(meta_obj, dict):
                    continue
                if 'r550_target' in meta_obj:
                    meta = dict(meta_obj)
                    meta.pop('r550_target', None)
                    hostile.meta = meta
            return

        for hostile in self.contacts:
            if not self._is_r550_interceptor(hostile):
                continue
            nearest = None
            nearest_nm = None
            nearest_xy: Optional[Tuple[float, float]] = None
            for shar in shar_contacts:
                if shar is hostile:
                    continue
                shar_xy = self._finite_xy(getattr(shar, 'x', 0.0), getattr(shar, 'y', 0.0))
                hostile_xy = self._finite_xy(getattr(hostile, 'x', 0.0), getattr(hostile, 'y', 0.0))
                if shar_xy is None or hostile_xy is None:
                    continue
                try:
                    dist = nm_distance(hostile_xy[0], hostile_xy[1], shar_xy[0], shar_xy[1])
                except Exception:
                    continue
                if not math.isfinite(dist):
                    continue
                if nearest_nm is None or dist < nearest_nm:
                    nearest_nm = dist
                    nearest = shar
                    nearest_xy = shar_xy

            meta_obj = getattr(hostile, 'meta', {}) or {}
            meta = dict(meta_obj) if isinstance(meta_obj, dict) else {}
            changed = False
            if nearest is not None and nearest_nm is not None and nearest_nm <= 15.0 and nearest_xy is not None:
                target_xy = nearest_xy
                target_payload = {
                    'id': getattr(nearest, 'id', None),
                    'x': target_xy[0],
                    'y': target_xy[1],
                    'range_nm': float(nearest_nm),
                }
                if meta.get('r550_target') != target_payload:
                    meta['r550_target'] = target_payload
                    changed = True
                if meta.pop('retreating', None) is not None:
                    changed = True
                if meta.pop('retreat_heading', None) is not None:
                    changed = True
                if meta.pop('retreat_since', None) is not None:
                    changed = True
                if getattr(hostile, 'threat', '') != 'high':
                    hostile.threat = 'high'
            else:
                if meta.pop('r550_target', None) is not None:
                    changed = True
                if meta.pop('retreating', None) is not None:
                    changed = True
                if meta.pop('retreat_heading', None) is not None:
                    changed = True
                if meta.pop('retreat_since', None) is not None:
                    changed = True

            if changed:
                hostile.meta = meta

    def force_spawn(self, own_x: float, own_y: float, allegiance: str, bearing_deg: float, range_nm: float) -> Contact:
        log_contact: Optional[Dict[str, Any]] = None
        log_spawn: Optional[Dict[str, Any]] = None

        with self._lock:
            ox, oy = self._origin_with_fallback(own_x, own_y)
            if math.isfinite(ox) and math.isfinite(oy):
                if abs(ox) >= 1e-3 or abs(oy) >= 1e-3:
                    self._last_own_xy = (ox, oy)
            r = float(range_nm)
            rad = math.radians(float(bearing_deg))
            dx = math.sin(rad) * r
            dy = -math.cos(rad) * r
            x = max(0.0, min(float(WORLD_N), ox + dx))
            y = max(0.0, min(float(WORLD_N), oy + dy))
            if str(allegiance).title() == 'Friendly':
                allegiance_norm = 'Friendly'
                name, speed, klass = self.catalog.pick_friendly()
                tries = 0
                while str(name).strip().lower().startswith('sea harrier') and tries < 8:
                    name, speed, klass = self.catalog.pick_friendly()
                    tries += 1
                if str(name).strip().lower().startswith('sea harrier'):
                    fallback_name = 'Type 42 Destroyer'
                    det = self.catalog.details(fallback_name) or {}
                    name = fallback_name
                    speed = float(det.get('speed_kts', 22.0))
                    klass = det.get('class', 'Ship')
                friendly_cells = self._occupied_cells()
                allow_overlap = str(klass or '').strip().lower() == 'aircraft'
                friendly_radius = max(self._nm_per_cell * 10.0, self._nm_per_cell)
                if r > friendly_radius:
                    r = friendly_radius
                    dx = math.sin(rad) * r
                    dy = -math.cos(rad) * r
                    x = clamp(ox + dx, 0.0, float(WORLD_N))
                    y = clamp(oy + dy, 0.0, float(WORLD_N))
                cell_label = self._cell_label_for_xy(x, y)
                if cell_label:
                    if (friendly_cells.get(cell_label) and not allow_overlap) or math.hypot(x - ox, y - oy) > (self._nm_per_cell * 10.0):
                        alt_xy = self._friendly_spawn_xy(ox, oy, klass, friendly_cells)
                        if alt_xy is not None:
                            x, y = alt_xy
                            rad = math.atan2(float(x) - float(ox), -(float(y) - float(oy)))
                            r = math.hypot(float(x) - float(ox), float(y) - float(oy))
                            bearing_deg = math.degrees(rad) % 360.0
            else:
                name, speed, klass = self.catalog.pick_hostile()
                allegiance_norm = 'Hostile'
            course_deg = (float(bearing_deg) + 180.0) % 360.0
            surface_meta: Optional[Dict[str, Any]] = None
            if allegiance_norm == 'Hostile' and str(klass or '').lower() == 'ship':
                surface_meta = {'hp': 4.0, 'max_hp': 4.0}
            meta = {
                "spawn": {"bearing_deg": round(float(bearing_deg), 1), "range_nm": round(r, 2), "surprise": False, "forced": True},
                "cap": self.catalog.details(name),
                "class": klass,
            }
            if surface_meta is not None:
                meta['surface_ship'] = surface_meta

            throttled_info = self._check_force_spawn_throttle(
                affiliation=allegiance_norm,
                contact_name=name,
                klass=klass,
                x=float(x),
                y=float(y),
                course_deg=course_deg,
                speed=float(speed),
                meta=meta,
            )
            throttled = bool(throttled_info.get("throttled") if isinstance(throttled_info, dict) else throttled_info)

            cid = int(self._next_id)
            self._next_id += 1
            contact = Contact(
                id=cid,
                name=name,
                allegiance=allegiance_norm,
                x=float(x),
                y=float(y),
                course_deg=course_deg,
                speed_kts=float(speed),
                threat="high" if name in ("Super Etendard", "Mirage III") else "medium",
                meta=meta,
            )
            self.contacts.append(contact)
            if allegiance_norm.lower() == 'hostile':
                self._pending_detection.add(contact.id)
            else:
                if not throttled:
                    log_contact = {
                        "id": contact.id,
                        "name": contact.name,
                        "allegiance": contact.allegiance,
                        "class": str(klass),
                        "world_xy": [round(contact.x, 2), round(contact.y, 2)],
                        "course_deg": contact.course_deg,
                        "speed_kts": contact.speed_kts * HOSTILE_SPEED_SCALE,
                    }
                elif throttled:
                    try:
                        max_contacts = int(self.cfg.get("max_contacts", 20) or 20)
                    except Exception:
                        max_contacts = 20
                    if len(self.contacts) > max_contacts:
                        allegiance_lc = contact.allegiance.lower()
                        removed = False
                        for idx, existing in enumerate(self.contacts):
                            if existing is contact:
                                continue
                            try:
                                if str(getattr(existing, 'allegiance', '')).lower() == allegiance_lc:
                                    self.contacts.pop(idx)
                                    removed = True
                                    break
                            except Exception:
                                continue
                        if not removed and len(self.contacts) > max_contacts:
                            self.contacts.pop(0)

            def _clamp(v, lo, hi):
                return lo if v < lo else hi if v > hi else v

            def _letters(i):
                s = ""
                n = max(1, int(i))
                while n > 0:
                    n -= 1
                    s = chr(ord('A') + (n % 26)) + s
                    n //= 26
                return s

            def _world_to_cell(row, col):
                def mv(v):
                    t = 1.0 + (_clamp(v, 0.0, float(WORLD_N)) * (BOARD_N - 1) / float(WORLD_N))
                    return int(round(_clamp(t, 1.0, float(BOARD_N))))

                r_i, c_i = mv(row), mv(col)
                return f"{_letters(c_i)}{r_i}"

            cell = _world_to_cell(y, x)
            if not throttled:
                log_spawn = {
                    "bearing_deg": round(float(bearing_deg), 1),
                    "range_nm": round(r, 2),
                    "chosen": {"name": name, "speed_kts": speed, "allegiance": allegiance_norm},
                    "target_world_xy": [round(x, 2), round(y, 2)],
                    "ship_world_xy": [round(ox, 2), round(oy, 2)],
                    "cell": cell,
                }

        if self.rec and log_contact and allegiance_norm.lower() != 'hostile':
            try:
                self.rec.log("radar.contact.new", log_contact)
            except Exception:
                pass
        if self.rec and log_spawn:
            try:
                self.rec.log("radar.force_spawn", log_spawn)
            except Exception:
                pass
        return contact

    def _check_force_spawn_throttle(
        self,
        *,
        affiliation: str,
        contact_name: str,
        klass: Any,
        x: float,
        y: float,
        course_deg: float,
        speed: float,
        meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Rate-limit forced spawns to avoid starving the UI with duplicate inserts.

        Returns a dict describing throttle status so the caller can decide how much to log.
        """
        try:
            now = time.time()
        except Exception:
            now = 0.0

        window = float(self.cfg.get("force_spawn_window_s", 3.0) or 3.0)
        per_sec_limit = int(self.cfg.get("force_spawn_rate_per_sec", 8) or 0)
        burst_limit_default = per_sec_limit * max(2, int(window)) if per_sec_limit > 0 else 0
        burst_limit = int(self.cfg.get("force_spawn_burst_limit", burst_limit_default) or 0)

        recent = self._force_spawn_recent
        try:
            while recent and (now - recent[0]) > window:
                recent.popleft()
        except Exception:
            recent.clear()

        try:
            recent_per_sec = sum(1 for t in recent if now - t <= 1.0)
        except Exception:
            recent_per_sec = len(recent)

        should_throttle = False
        reason = None
        if per_sec_limit > 0 and recent_per_sec >= per_sec_limit:
            should_throttle = True
            reason = "per_sec"
        elif burst_limit > 0 and len(recent) >= burst_limit:
            should_throttle = True
            reason = "burst"

        result = {"throttled": False, "reason": None, "per_sec": recent_per_sec, "burst": len(recent)}

        if not should_throttle:
            recent.append(now)
            return result

        result["throttled"] = True
        result["reason"] = reason or "rate_limit"
        recent.append(now)
        if self.rec:
            try:
                last = getattr(self, "_force_spawn_throttle_last_log", 0.0)
            except Exception:
                last = 0.0
            if now - last >= 5.0:
                try:
                    self.rec.log("radar.force_spawn_throttled", {
                        "reason": result["reason"],
                        "per_sec": recent_per_sec,
                        "burst": len(recent),
                        "allegiance": str(affiliation or '').lower(),
                    })
                except Exception:
                    pass
                self._force_spawn_throttle_last_log = now

        return result

    # ---- Resupply Sea King injection ---------------------------------------
    def _sync_resupply_contact(self) -> None:
        if self.resupply_state_provider is None:
            return
        try:
            st = self.resupply_state_provider() or {}
        except Exception:
            st = {}
        active = bool(st.get('active', False))
        stage = str(st.get('stage') or '')
        if not active or stage not in ('enroute', 'landing'):
            # Remove existing
            if self._resupply_contact_id is not None:
                self.contacts = [c for c in self.contacts if int(getattr(c,'id',-1)) != int(self._resupply_contact_id)]
                self._resupply_contact_id = None
            return
        origin_cell = str(st.get('origin_cell') or st.get('cell') or '').strip().upper()
        target_cell = str(st.get('target_cell') or origin_cell).strip().upper()
        origin_xy = st.get('origin_xy') if isinstance(st.get('origin_xy'), (tuple, list)) else None
        target_xy = st.get('target_xy') if isinstance(st.get('target_xy'), (tuple, list)) else None
        try:
            from projects.falklandV2.grid.mapping import label_to_world  # late import
        except Exception:
            label_to_world = None  # type: ignore
        if origin_xy is None and origin_cell and label_to_world is not None:
            try:
                ox, oy = label_to_world(origin_cell, world_n=float(WORLD_N))
                origin_xy = (float(ox), float(oy))
            except Exception:
                origin_xy = None
        if target_xy is None and target_cell and label_to_world is not None:
            try:
                tx, ty = label_to_world(target_cell, world_n=float(WORLD_N))
                target_xy = (float(tx), float(ty))
            except Exception:
                target_xy = origin_xy
        if origin_xy is None:
            return
        if target_xy is None:
            target_xy = origin_xy
        start_ts = float(st.get('started_ts', 0.0) or 0.0)
        eta_ts = float(st.get('eta_ts', 0.0) or 0.0)
        if stage == 'landing':
            progress = 1.0
        elif eta_ts > start_ts:
            progress = clamp((time.time() - start_ts) / (eta_ts - start_ts), 0.0, 1.0)
        else:
            progress = 0.0
        ox, oy = origin_xy
        tx, ty = target_xy
        x = float(ox + (tx - ox) * progress)
        y = float(oy + (ty - oy) * progress)
        if self._resupply_contact_id is not None:
            c = next((k for k in self.contacts if int(getattr(k,'id',-1)) == int(self._resupply_contact_id)), None)
            if c is not None:
                c.x = float(x); c.y = float(y)
                try:
                    meta = getattr(c, 'meta', {})
                    if isinstance(meta, dict):
                        meta['resupply'] = True
                        meta['stage'] = stage
                        meta['resupply_stage'] = stage
                        c.meta = meta
                except Exception:
                    pass
                return
        # Create contact
        cid = int(self._next_id); self._next_id += 1
        meta = {'resupply': True, 'stage': stage, 'resupply_stage': stage}
        c = Contact(id=cid, name='Sea King Helicopter', allegiance='Friendly', x=float(x), y=float(y), course_deg=0.0, speed_kts=90.0, threat='low', meta=meta)
        self.contacts.append(c)
        self._resupply_contact_id = cid

    # ---- CAP contact injection --------------------------------------------
    @staticmethod
    def _compute_nm_per_cell() -> float:
        try:
            from projects.falklandV2.grid.mapping import label_to_world  # late import
            ax, ay = label_to_world('AA00', world_n=float(WORLD_N))
            bx, by = label_to_world('AA01', world_n=float(WORLD_N))
            dist = math.hypot(float(bx) - float(ax), float(by) - float(ay))
            return dist if dist > 0.0 else 1.0
        except Exception:
            return 1.0

    def _enemy_spacing_nm(self) -> float:
        return max(1.0, float(self._enemy_spacing_cells) * float(self._nm_per_cell or 1.0))

    def _cell_label_for_xy(self, x_val: float, y_val: float) -> str:
        try:
            from projects.falklandV2.grid.mapping import world_to_label  # late import
            return world_to_label(float(x_val), float(y_val), world_n=float(WORLD_N))
        except Exception:
            return ''

    def _occupied_cells(self) -> Dict[str, List[Contact]]:
        occupied: Dict[str, List[Contact]] = {}
        for contact in self.contacts:
            try:
                label = self._cell_label_for_xy(float(getattr(contact, 'x', 0.0)), float(getattr(contact, 'y', 0.0)))
            except Exception:
                label = ''
            if label:
                occupied.setdefault(label, []).append(contact)
        return occupied

    def _friendly_spawn_xy(self, own_x: float, own_y: float, klass: Optional[str], occupied: Dict[str, List[Contact]]) -> Optional[Tuple[float, float]]:
        allow_overlap = str(klass or '').strip().lower() == 'aircraft'
        max_radius = max(self._nm_per_cell * 10.0, self._nm_per_cell)
        min_radius = max(self._nm_per_cell * 0.5, 0.25)
        for _ in range(40):
            radius = self.rng.uniform(min_radius, max_radius)
            bearing = self.rng.uniform(0.0, 360.0)
            rad = math.radians(bearing)
            x = clamp(own_x + math.sin(rad) * radius, 0.0, float(WORLD_N))
            y = clamp(own_y - math.cos(rad) * radius, 0.0, float(WORLD_N))
            label = self._cell_label_for_xy(x, y)
            if not label:
                continue
            if not allow_overlap and label in occupied:
                continue
            return (x, y)
        return None

    def _formation_wingman_position(self, leader_x: float, leader_y: float, spacing_nm: float, ux: float, uy: float) -> Tuple[float, float]:
        def _clamp_xy(x_val: float, y_val: float) -> Tuple[float, float]:
            return (
                clamp(x_val, 0.0, float(WORLD_N)),
                clamp(y_val, 0.0, float(WORLD_N)),
            )

        candidates: List[Tuple[float, float]] = []
        # Primary trailing position (aft along flight path)
        candidates.append((leader_x - ux * spacing_nm, leader_y - uy * spacing_nm))
        # Lateral offsets (port/starboard) to keep spacing if trailing is clipped by bounds
        px, py = -uy, ux
        candidates.append((leader_x + px * spacing_nm, leader_y + py * spacing_nm))
        candidates.append((leader_x - px * spacing_nm, leader_y - py * spacing_nm))

        threshold = spacing_nm * 0.75
        for cx, cy in candidates:
            clamped = _clamp_xy(cx, cy)
            if math.hypot(leader_x - clamped[0], leader_y - clamped[1]) >= threshold:
                return clamped

        # Fallback: choose candidate that yields the greatest separation available
        cx, cy = max(
            candidates,
            key=lambda pt: math.hypot(leader_x - clamp(pt[0], 0.0, float(WORLD_N)), leader_y - clamp(pt[1], 0.0, float(WORLD_N)))
        )
        return _clamp_xy(cx, cy)

    @staticmethod
    def _normalize_cell_label(cell: str) -> str:
        s = str(cell or '').strip().upper()
        if not s:
            return ''
        # Already canonical (two letters)
        if re.fullmatch(r'[A-Z]{2}\d{1,3}', s):
            return s
        # Legacy single-letter columns (e.g., K13 -> AK13)
        m = re.fullmatch(r'([A-Z])(\d{1,3})', s)
        if m:
            return f"A{m.group(1)}{m.group(2)}"
        return s

    @staticmethod
    def _cell_to_world(cell: str) -> Tuple[float, float]:
        # Prefer canonical mapping
        label = Radar._normalize_cell_label(cell)
        if label:
            try:
                from projects.falklandV2.grid.mapping import label_to_world  # late import
                return label_to_world(label, world_n=float(WORLD_N))
            except Exception:
                pass
        # Fallback to legacy 26×26 board approximation
        s = str(cell or '').strip().upper()
        if not s:
            return (float(WORLD_N) / 2.0, float(WORLD_N) / 2.0)
        i = 0
        while i < len(s) and s[i].isalpha():
            i += 1
        letters = s[:i] or 'A'
        digits = s[i:] or '1'
        col_idx = 0
        for ch in letters:
            if 'A' <= ch <= 'Z':
                col_idx = col_idx * 26 + (ord(ch) - ord('A') + 1)
        try:
            row_idx = int(digits)
        except Exception:
            row_idx = 1
        col_idx = max(1, min(BOARD_N, col_idx)) - 1
        row_idx = max(1, min(BOARD_N, row_idx)) - 1
        board_min = (float(WORLD_N) - float(BOARD_N)) / 2.0
        x = board_min + float(col_idx)
        y = board_min + float(row_idx)
        return (x, y)

    @staticmethod
    def _cell_to_index(cell: str) -> Tuple[int, int]:
        label = Radar._normalize_cell_label(cell)
        if label:
            try:
                from projects.falklandV2.grid.coords import to_index  # late import
                return to_index(label)
            except Exception:
                pass
        s = str(cell or '').strip().upper()
        if not s:
            mid = max(0, BOARD_N // 2)
            return (mid, mid)
        i = 0
        while i < len(s) and s[i].isalpha():
            i += 1
        letters = s[:i] or 'A'
        digits = s[i:] or '1'
        col_idx = 0
        for ch in letters:
            if 'A' <= ch <= 'Z':
                col_idx = col_idx * 26 + (ord(ch) - ord('A') + 1)
        try:
            row_idx = int(digits)
        except Exception:
            row_idx = 1
        col_idx = max(1, min(BOARD_N, col_idx)) - 1
        row_idx = max(1, min(BOARD_N, row_idx)) - 1
        return (col_idx, row_idx)

    @staticmethod
    def _index_to_cell(col_idx: int, row_idx: int) -> str:
        try:
            from projects.falklandV2.grid.coords import from_index  # late import
            return Radar._normalize_cell_label(from_index(int(col_idx), int(row_idx)))
        except Exception:
            cc = max(0, min(BOARD_N - 1, int(col_idx)))
            rr = max(0, min(BOARD_N - 1, int(row_idx)))
            n = cc + 1
            letters = ''
            while n > 0:
                n, rem = divmod(n - 1, 26)
                letters = chr(ord('A') + rem) + letters
            digits = str(rr + 1)
            return f"{letters}{digits}"

    def _wingman_cell(self, base_cell: str, base_xy: Tuple[float, float]) -> Tuple[str, Tuple[float, float]]:
        try:
            from projects.falklandV2.grid.config import MASTER_COLS, MASTER_ROWS  # late import
        except Exception:
            MASTER_COLS = MASTER_ROWS = 40
        try:
            from projects.falklandV2.grid.coords import to_index, from_index  # late import
        except Exception:
            to_index = from_index = None  # type: ignore
        try:
            from projects.falklandV2.grid.mapping import world_to_label, label_to_world  # late import
        except Exception:
            world_to_label = label_to_world = None  # type: ignore

        label = ''
        if world_to_label is not None:
            try:
                label = world_to_label(float(base_xy[0]), float(base_xy[1]), world_n=float(WORLD_N))
            except Exception:
                label = ''
        if not label:
            label = self._normalize_cell_label(base_cell)

        col_idx = row_idx = None
        if label and to_index is not None:
            try:
                col_idx, row_idx = to_index(label)
            except Exception:
                col_idx = row_idx = None

        if (col_idx is None or row_idx is None) and world_to_label is not None:
            try:
                col_idx, row_idx = to_index(world_to_label(float(base_xy[0]), float(base_xy[1]), world_n=float(WORLD_N))) if to_index is not None else (None, None)
            except Exception:
                col_idx = row_idx = None

        if col_idx is None or row_idx is None:
            col_idx, row_idx = self._cell_to_index(label or base_cell)

        offsets = [
            (0, 1),   # six o'clock (south)
            (0, -1),  # twelve o'clock
            (-1, 0),  # nine o'clock
            (1, 0),   # three o'clock
            (-1, 1),  # southwest
            (1, 1),   # southeast
            (-1, -1), # northwest
            (1, -1),  # northeast
        ]
        for dc, dr in offsets:
            nc, nr = col_idx + dc, row_idx + dr
            if 0 <= nc < MASTER_COLS and 0 <= nr < MASTER_ROWS:
                if from_index is not None:
                    try:
                        cell = from_index(nc, nr)
                    except Exception:
                        cell = self._index_to_cell(nc, nr)
                else:
                    cell = self._index_to_cell(nc, nr)
                if label_to_world is not None:
                    try:
                        xy = label_to_world(cell, world_n=float(WORLD_N))
                        return (cell, xy)
                    except Exception:
                        pass
                return (cell, self._cell_to_world(cell))

        fallback_label = label or base_cell
        return (fallback_label, self._cell_to_world(fallback_label))

    def _sync_hostile_formations(self, own_x: float, own_y: float) -> None:
        if not self._enemy_formations:
            return
        id_map: Dict[int, Contact] = {c.id: c for c in self.contacts}
        min_spacing_nm = self._enemy_spacing_nm()
        removals: List[Tuple[int, int, int]] = []
        for fid, info in list(self._enemy_formations.items()):
            leader_id = int(info.get("leader_id", -1))
            wingman_id = int(info.get("wingman_id", -1))
            leader = id_map.get(leader_id)
            wingman = id_map.get(wingman_id)
            if leader is None or wingman is None:
                removals.append((fid, leader_id, wingman_id))
                continue
            spacing_nm = float(info.get("spacing_nm", min_spacing_nm) or min_spacing_nm)
            dist_to_ship = nm_distance(float(leader.x), float(leader.y), float(own_x), float(own_y))
            if dist_to_ship <= 5.0:
                spacing_nm = max(spacing_nm, min_spacing_nm)
            info["spacing_nm"] = spacing_nm
            rad = math.radians(leader.course_deg)
            ux = math.sin(rad)
            uy = -math.cos(rad)
            if abs(ux) < 1e-6 and abs(uy) < 1e-6:
                dx = float(own_x) - float(leader.x)
                dy = float(own_y) - float(leader.y)
                norm = math.hypot(dx, dy)
                if norm > 1e-6:
                    ux, uy = dx / norm, dy / norm
                else:
                    ux, uy = 0.0, -1.0
            wingman.course_deg = leader.course_deg
            wingman.speed_kts = leader.speed_kts
            target_x, target_y = self._formation_wingman_position(float(leader.x), float(leader.y), spacing_nm, ux, uy)
            wingman.x = target_x
            wingman.y = target_y
            wingman.meta.setdefault('formation', {})
            wingman.meta['formation'].update({
                "id": fid,
                "role": "wingman",
                "leader_id": leader.id,
                "spacing_cells": self._enemy_spacing_cells,
            })
            leader.meta.setdefault('formation', {})
            leader.meta['formation'].update({
                "id": fid,
                "role": "leader",
                "wingman_id": wingman.id,
                "spacing_cells": self._enemy_spacing_cells,
            })
        for fid, leader_id, wingman_id in removals:
            self._enemy_formations.pop(fid, None)
            leader = id_map.get(leader_id)
            if leader and leader.meta.get('formation', {}).get('id') == fid:
                leader.meta.pop('formation', None)
            wingman = id_map.get(wingman_id)
            if wingman and wingman.meta.get('formation', {}).get('id') == fid:
                wingman.meta.pop('formation', None)

    def _cap_contact_layout(self, mid: int, mission: Dict[str, Any], base_x: float, base_y: float, base_cell: str, cap_name: str) -> List[Dict[str, Any]]:
        base_label = ''
        try:
            from projects.falklandV2.grid.mapping import world_to_label  # late import
            base_label = world_to_label(float(base_x), float(base_y), world_n=float(WORLD_N))
        except Exception:
            base_label = ''
        if not base_label:
            base_label = self._normalize_cell_label(base_cell)
        wing_cell, wing_xy = self._wingman_cell(base_label, (base_x, base_y))
        base_index = max(1, int(mid))
        leader_num = (base_index - 1) * 2 + 1
        wingman_num = leader_num + 1
        lead_callsign = f"S{leader_num}"
        wing_callsign = f"S{wingman_num}"
        layout = [
            {
                "role": "leader",
                "x": float(base_x),
                "y": float(base_y),
                "cell": base_label or wing_cell,
                "callsign": lead_callsign,
                "display_name": f"{cap_name} ({lead_callsign})",
                "mission": mission,
            },
            {
                "role": "wingman",
                "x": float(wing_xy[0]),
                "y": float(wing_xy[1]),
                "cell": wing_cell if wing_cell else base_label,
                "callsign": wing_callsign,
                "display_name": f"{cap_name} ({wing_callsign})",
                "mission": mission,
            },
        ]
        return layout

    def _sync_cap_contacts(self) -> None:
        if self.cap_missions_provider is None:
            return
        missions: List[Dict[str, Any]] = []
        try:
            missions = list(self.cap_missions_provider() or [])
        except Exception:
            missions = []

        # Active-air statuses
        ACTIVE = {"queued", "airborne", "onstation", "rtb", "recovering"}
        want: Dict[int, List[Dict[str, Any]]] = {}
        cap_name = 'Sea Harrier FRS.1'
        for m in missions:
            try:
                status = str(m.get('status') or '').lower()
                if status not in ACTIVE:
                    continue
                mid = int(m.get('id'))
                pos_xy = m.get('position_xy')
                x = y = None
                if isinstance(pos_xy, (list, tuple)) and len(pos_xy) >= 2:
                    try:
                        x = float(pos_xy[0])
                        y = float(pos_xy[1])
                    except Exception:
                        x = y = None
                # Choose placement by status: queued/rtb/recovering at origin; onstation/airborne at target
                if status in ("queued", "rtb", "recovering"):
                    cell = str(m.get('origin_cell') or m.get('cur_cell') or m.get('target_cell') or '').strip()
                else:
                    cell = str(m.get('cur_cell') or m.get('target_cell') or m.get('origin_cell') or '').strip()
                if (x is None or y is None):
                    if not cell:
                        continue
                    x, y = self._cell_to_world(cell)
                elif not cell:
                    try:
                        from projects.falklandV2.grid.mapping import world_to_label  # late import to avoid cycles
                        cell = world_to_label(float(x), float(y), world_n=float(WORLD_N))
                    except Exception:
                        cell = ''
                if x is None or y is None:
                    continue
                want[mid] = self._cap_contact_layout(mid, m, x, y, cell, cap_name)
            except Exception:
                continue

        # Remove stale CAP contacts
        for mid, role_map in list(self._cap_contacts.items()):
            if mid not in want:
                # Drop contacts with ids in role_map if present
                for cid in role_map.values():
                    self.contacts = [c for c in self.contacts if int(getattr(c, 'id', -1)) != int(cid)]
                self._cap_contacts.pop(mid, None)

        # Ensure/update required CAP contacts
        # Resolve catalog details (capability + speed)
        # Try to find a speed value for the CAP type
        try:
            # Probe friendly pool to find matching speed
            # Note: Catalog intentionally excludes Harrier from friendlies; fall back to 420
            speed_kts = float(self.catalog._details.get(cap_name, {}).get('speed_kts') or 420.0)
        except Exception:
            speed_kts = 420.0
        now_ts = time.time()
        tau = getattr(math, "tau", 2.0 * math.pi)
        for mid, layout in want.items():
            mission = layout[0].get("mission") if layout else {}
            follow_mode = str((mission or {}).get("follow") or "").strip().lower()
            status = str((mission or {}).get("status") or "").strip().lower()
            # Clean up any stale legacy contacts for this mission that lack flight_role metadata
            for contact in list(self.contacts):
                try:
                    meta = getattr(contact, 'meta', {}) or {}
                    if not meta.get('cap_flight'):
                        continue
                    if int(meta.get('mission_id', -1)) != int(mid):
                        continue
                    role = str(meta.get('flight_role') or '').strip().lower()
                    if role not in ('leader', 'wingman'):
                        self.contacts = [c for c in self.contacts if int(getattr(c, 'id', -1)) != int(getattr(contact, 'id', -1))]
                except Exception:
                    continue
            # Recompute orbiting positions for leader entry if following Hermes
            if follow_mode == "hermes" and status in ("airborne", "onstation"):
                base_cell = str((mission or {}).get("target_cell") or layout[0].get("cell") or "")
                base_x, base_y = self._cell_to_world(base_cell) if base_cell else (layout[0].get("x", 0.0), layout[0].get("y", 0.0))
                try:
                    radius = float((mission or {}).get("station_radius_nm") or 0.0)
                except Exception:
                    radius = 0.0
                if radius <= 0.0:
                    radius = float(self.cfg.get("hermes_follow_radius_nm", 5.0))
                period = float(self.cfg.get("hermes_follow_orbit_period_s", 240.0))
                if period <= 0.0:
                    period = 240.0
                theta_base = (now_ts / period) + (mid * 0.35)
                orbit_x = base_x + radius * math.cos(theta_base * tau)
                orbit_y = base_y + radius * math.sin(theta_base * tau)
                if status == "airborne":
                    ts = mission.get("timestamps") if isinstance(mission, dict) else {}
                    try:
                        start_time = float((ts or {}).get("airborne", (ts or {}).get("launch", now_ts)))
                    except Exception:
                        start_time = now_ts
                    try:
                        eta_on = float((ts or {}).get("eta_onstation", start_time))
                    except Exception:
                        eta_on = start_time
                    elapsed = max(0.0, now_ts - start_time)
                    duration = max(1e-3, eta_on - start_time)
                    prog = max(0.0, min(1.0, elapsed / duration))
                    origin_xy = None
                    try:
                        ox, oy = mission.get("origin_xy") or ()
                        origin_xy = (float(ox), float(oy))
                    except Exception:
                        origin_xy = None
                    if origin_xy is None:
                        origin_cell = str((mission or {}).get("origin_cell") or "")
                        if origin_cell:
                            origin_xy = self._cell_to_world(origin_cell)
                    if origin_xy is None:
                        origin_xy = (base_x, base_y)
                    leader_x = float(origin_xy[0]) + (orbit_x - float(origin_xy[0])) * prog
                    leader_y = float(origin_xy[1]) + (orbit_y - float(origin_xy[1])) * prog
                else:
                    leader_x, leader_y = orbit_x, orbit_y
                layout[0]["x"] = float(leader_x)
                layout[0]["y"] = float(leader_y)
                layout[0]["cell"] = str(layout[0].get("cell") or base_cell or "")
                # Recompute wingman position based on updated leader position
                wing_cell, wing_xy = self._wingman_cell(layout[0]["cell"], (leader_x, leader_y))
                if len(layout) > 1:
                    layout[1]["x"] = float(wing_xy[0])
                    layout[1]["y"] = float(wing_xy[1])
                    layout[1]["cell"] = wing_cell if wing_cell else layout[0]["cell"]

            existing_map = self._cap_contacts.get(mid, {})
            if not isinstance(existing_map, dict):
                existing_map = {}
            updated_map: Dict[str, int] = {}
            for entry in layout:
                role = str(entry.get("role") or "").strip().lower() or "leader"
                x = float(entry.get("x", 0.0))
                y = float(entry.get("y", 0.0))
                cell = str(entry.get("cell") or "").strip()
                callsign = str(entry.get("callsign") or "")
                display_name = str(entry.get("display_name") or cap_name)
                mission_data = entry.get("mission") or {}
                cid = existing_map.get(role)
                contact_obj = None
                if cid is not None:
                    contact_obj = next((k for k in self.contacts if int(getattr(k, 'id', -1)) == int(cid)), None)
                if contact_obj is not None:
                    contact_obj.x = float(x)
                    contact_obj.y = float(y)
                    meta = dict(getattr(contact_obj, 'meta', {}) or {})
                    meta.update({
                        'cap_flight': True,
                        'mission_id': mid,
                        'station_cell': cell,
                        'kind': 'cap',
                        'cap': self.catalog.details(cap_name),
                        'callsign': callsign,
                        'display_name': display_name,
                        'flight_role': role,
                    })
                    contact_obj.meta = meta
                else:
                    cid = int(self._next_id)
                    self._next_id += 1
                    meta = {
                        'cap_flight': True,
                        'mission_id': mid,
                        'station_cell': cell,
                        'kind': 'cap',
                        'cap': self.catalog.details(cap_name),
                        'callsign': callsign,
                        'display_name': display_name,
                        'flight_role': role,
                    }
                    contact_obj = Contact(
                        id=cid,
                        name=cap_name,
                        allegiance='Friendly',
                        x=float(x), y=float(y),
                        course_deg=0.0,
                        speed_kts=float(speed_kts),
                        threat='low',
                        meta=meta,
                    )
                    self.contacts.append(contact_obj)
                updated_map[role] = cid

            # Remove any stale role-specific contacts not present in updated_map
            for role, cid in list(existing_map.items()):
                if role not in updated_map:
                    self.contacts = [c for c in self.contacts if int(getattr(c, 'id', -1)) != int(cid)]
            if updated_map:
                self._cap_contacts[mid] = updated_map
            else:
                self._cap_contacts.pop(mid, None)

    def _apply_harrier_deterrence(self) -> None:
        harriers = [c for c in self.contacts if str(getattr(c, 'allegiance', '')) == 'Friendly' and bool(getattr(c, 'meta', {}).get('cap_flight'))]
        if not harriers:
            return
        now_ts = time.time()
        for hostile in self.contacts:
            try:
                if str(getattr(hostile, 'allegiance', '')) != 'Hostile':
                    continue
                if self._is_r550_interceptor(hostile):
                    continue
                if str(getattr(hostile, 'meta', {}).get('kind', '')) == 'missile':
                    continue
                meta = getattr(hostile, 'meta', {})
                # Allow retreating aircraft to stay disengaged without re-rolling
                if meta.get('retreating'):
                    continue
                nearest = None
                nearest_nm = None
                for harrier in harriers:
                    d = nm_distance(hostile.x, hostile.y, harrier.x, harrier.y)
                    if nearest_nm is None or d < nearest_nm:
                        nearest_nm = d
                        nearest = harrier
                if nearest is None or nearest_nm is None or nearest_nm > 10.0:
                    continue
                if self.rng.random() >= 0.30:
                    continue
                hx, hy = nearest.x, nearest.y
                dx = hostile.x - hx
                dy = hostile.y - hy
                if abs(dx) < 1e-6 and abs(dy) < 1e-6:
                    continue
                retreat_heading = math.degrees(math.atan2(dx, -dy)) % 360.0
                hostile.course_deg = retreat_heading
                meta['retreating'] = True
                meta['retreat_heading'] = retreat_heading
                meta['retreat_since'] = now_ts
                hostile.threat = 'low'
                hostile.meta = meta
                if self.rec:
                    try:
                        self.rec.log('radar.retreat', {'id': hostile.id, 'name': hostile.name, 'range_nm': round(nearest_nm, 2)})
                    except Exception:
                        pass
                try:
                    from projects.falklandV2 import webdash as wd  # type: ignore
                    if hasattr(wd, 'record_event'):
                        wd.record_event('pilot.bandit.retreat', {
                            'id': getattr(hostile, 'id', None),
                            'name': getattr(hostile, 'name', 'Bandit'),
                            'range_nm': round(nearest_nm, 2) if nearest_nm is not None else None,
                            'heading_deg': round(retreat_heading, 1)
                        })
                except Exception:
                    pass
            except Exception:
                continue

    def _prune_retreating_contacts(self, own_x: float, own_y: float) -> None:
        try:
            margin = float(self.cfg.get("retreat_despawn_margin_nm", 5.0))
        except Exception:
            margin = 5.0
        try:
            horizon = float(self.cfg.get("offboard_max_nm", 40.0))
        except Exception:
            horizon = 40.0
        limit_nm = max(0.0, horizon) + max(0.0, margin)
        world_margin = max(0.0, margin)
        survivors: List[Contact] = []
        removed: List[Tuple[Contact, float]] = []
        for contact in self.contacts:
            try:
                if str(getattr(contact, 'allegiance', '')).lower() != 'hostile':
                    survivors.append(contact)
                    continue
                meta = getattr(contact, 'meta', {}) or {}
                if not meta.get('retreating'):
                    survivors.append(contact)
                    continue
                if str(meta.get('kind', '')).lower() == 'missile':
                    survivors.append(contact)
                    continue
                rng = nm_distance(float(contact.x), float(contact.y), float(own_x), float(own_y))
                x = float(getattr(contact, 'x', 0.0))
                y = float(getattr(contact, 'y', 0.0))
                if rng < limit_nm and (-world_margin <= x <= float(WORLD_N) + world_margin) and (-world_margin <= y <= float(WORLD_N) + world_margin):
                    survivors.append(contact)
                    continue
                removed.append((contact, rng))
            except Exception:
                survivors.append(contact)
        if not removed:
            return
        self.contacts = survivors
        for contact, _rng in removed:
            try:
                cid = int(getattr(contact, 'id', -1))
                self._pending_detection.discard(cid)
            except Exception:
                pass
            try:
                formation = getattr(contact, 'meta', {}).get('formation', {})
                fid = int(formation.get('id')) if isinstance(formation, dict) and formation.get('id') is not None else None
            except Exception:
                fid = None
            if fid is not None:
                self._enemy_formations.pop(fid, None)
                for other in self.contacts:
                    try:
                        meta = getattr(other, 'meta', {}) or {}
                        f = meta.get('formation') if isinstance(meta, dict) else None
                        oid = int(f.get('id')) if isinstance(f, dict) and f.get('id') is not None else None
                    except Exception:
                        oid = None
                    if oid == fid:
                        try:
                            meta.pop('formation', None)
                            other.meta = meta
                        except Exception:
                            pass
            if self.priority_id == getattr(contact, 'id', None):
                self.priority_id = None
        if self.rec:
            for contact, rng in removed:
                try:
                    self.rec.log("radar.retreat_despawn", {
                        "id": int(getattr(contact, 'id', -1)),
                        "name": getattr(contact, 'name', 'Bandit'),
                        "range_nm": round(rng, 2),
                        "world_xy": [
                            round(float(getattr(contact, 'x', 0.0)), 2),
                            round(float(getattr(contact, 'y', 0.0)), 2),
                        ],
                    })
                except Exception:
                    pass

    def set_manual_lock(self, contact_id: Optional[int]) -> None:
        if contact_id is None:
            self.priority_id = None
            self._manual_lock = False
            return
        try:
            cid = int(contact_id)
        except Exception:
            self.priority_id = None
            self._manual_lock = False
            return
        self.priority_id = cid
        self._manual_lock = True

    def clear_manual_lock(self) -> None:
        self.priority_id = None
        self._manual_lock = False

    def seed_test_contacts(self, own_x: float, own_y: float, count: int = 10) -> None:
        """Pre-fill the contact list with a friendly/hostile mix for test runs."""
        try:
            target = int(count)
        except Exception:
            target = 0
        if target <= 0:
            return

        max_contacts = int(self.cfg.get("max_contacts", 10) or 10)
        target = min(target, max_contacts)
        if len(self.contacts) >= target:
            return

        span = self.cfg.get("no_spawn_nm", [15.0, 20.0])
        try:
            base_min = float(span[0]) if isinstance(span, list) and span else 15.0
        except Exception:
            base_min = 15.0
        try:
            base_max = float(self.cfg.get("offboard_max_nm", 40.0))
        except Exception:
            base_max = 40.0
        if base_min >= base_max:
            base_min = max(1.0, min(base_min, base_max - 1.0))

        remaining = target - len(self.contacts)
        hostiles = max(1, (remaining + 1) // 2)
        friendlies = max(1 if remaining > 1 else 0, remaining - hostiles)
        mix = (["Hostile"] * hostiles) + (["Friendly"] * friendlies)
        self.rng.shuffle(mix)

        for allegiance in mix:
            if len(self.contacts) >= target or len(self.contacts) >= max_contacts:
                break
            rng_nm = self.rng.uniform(base_min, base_max)
            bearing = self.rng.uniform(0.0, 360.0)
            self.force_spawn(own_x, own_y, allegiance, bearing, rng_nm)

    def _select_priority(self, own_x: float, own_y: float):
        if self._manual_lock:
            try:
                pid = int(self.priority_id) if self.priority_id is not None else None
            except Exception:
                pid = None
            if pid is not None:
                existing = next((c for c in self.contacts if int(getattr(c, 'id', -1)) == pid), None)
                if existing is not None:
                    return
            self._manual_lock = False
            self.priority_id = None

        if self.priority_id is not None:
            try:
                pid = int(self.priority_id)
            except Exception:
                pid = None
            if pid is not None:
                existing = next((c for c in self.contacts if int(getattr(c, 'id', -1)) == pid), None)
                if existing is not None:
                    return
        self.priority_id = None

    def _check_close_alarm(self, own_x: float, own_y: float):
        if self.priority_id is None or not self.rec:
            return
        c = next((k for k in self.contacts if k.id == self.priority_id), None)
        if not c or c.allegiance != "Hostile":
            return
        rng = nm_distance(c.x, c.y, own_x, own_y)
        if rng <= self.cfg["close_threat_nm"]:
            now = time.time()
            if now - c.last_warn_close >= self.cfg["close_alarm_cooldown_s"]:
                c.last_warn_close = now
                self.rec.log("ship.alarm.threat_close", {
                    "id": c.id, "name": c.name, "range_nm": round(rng, 2),
                    "world_xy": [round(c.x,2), round(c.y,2)]
                })
