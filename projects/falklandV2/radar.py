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
import math, random, time, json, os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple, Callable

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
        retreat_heading = None
        if self.allegiance == "Hostile" and str(self.meta.get('kind','')) != 'missile':
            if self.meta.get('retreating'):
                try:
                    retreat_heading = float(self.meta.get('retreat_heading', self.course_deg))
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
                self.meta['retreat_heading'] = retreat_heading

        if self.speed_kts > 0:
            nm = (self.speed_kts * HOSTILE_SPEED_SCALE) * (dt_s / 3600.0)
            rad = math.radians(self.course_deg)
            dx = math.sin(rad) * nm
            dy = -math.cos(rad) * nm
            self.x = clamp(self.x + dx, 0.0, float(WORLD_N))
            self.y = clamp(self.y + dy, 0.0, float(WORLD_N))

# --- Radar -------------------------------------------------------------------
class Radar:
    def __init__(self, rec=None, cfg: Optional[dict] = None, rng: Optional[random.Random] = None, catalog_path: Optional[str] = None):
        self.rec = rec
        self.rng = rng or random.Random()
        self.cfg = {
            "scan_interval_s": 180,
            "no_spawn_nm": [15.0, 20.0],
            "surprise_nm": 10.0,
            "offboard_max_nm": 40.0,
            "max_contacts": 20,
            "close_threat_nm": 3.0,
            "close_alarm_cooldown_s": 30.0,
            # Probability a normal (non-surprise) spawn is Friendly instead of Hostile
            "friendly_prob": 0.3,
            # Time-based spawn rates (per minute), decoupled from scans
            # Roughly matches old behavior (~0.5 spawns per 3 minutes → ~0.166/min),
            # with ~1/3 of those being "surprise" spawns.
            "spawn_rate_per_min": 0.1667,
            "surprise_rate_per_min": 0.0556,
        }
        if cfg: self.cfg.update(cfg)
        self.contacts: List[Contact] = []
        self._accum = 0.0
        self._next_id = 1
        self.priority_id: Optional[int] = None
        self._manual_lock = False
        # Optional CAP effects provider (callable returning dict with keys: active, effects)
        self.cap_effects_provider: Optional[Callable[[], Dict[str, Any]]] = None
        # Optional CAP missions provider for placing friendly CAP flights on radar
        # Expected to return a list of dicts with keys: id, status, target_cell, origin_cell (optional)
        self.cap_missions_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None
        # Track injected CAP contacts by mission id -> contact id
        self._cap_contacts: Dict[int, int] = {}
        # Optional resupply state provider (returns dict with keys: active, stage, origin_cell)
        self.resupply_state_provider: Optional[Callable[[], Dict[str, Any]]] = None
        self._resupply_contact_id: Optional[int] = None
        # Catalog
        if catalog_path:
            self.catalog = Catalog(catalog_path, rng=self.rng)
        else:
            # best-effort default: relative to this file
            default_path = os.path.join(os.path.dirname(__file__), 'data', 'contacts.json')
            self.catalog = Catalog(default_path, rng=self.rng)

    # API
    def tick(self, dt_s: float, own_x: float, own_y: float):
        # cadence
        self._accum += dt_s
        if self._accum >= self.cfg["scan_interval_s"]:
            self._accum = 0.0
            self.scan(own_x, own_y)

        # time-based spawn chance (Poisson process per minute)
        try:
            # Clamp dt to sane bounds
            dt = max(0.0, min(float(dt_s), 5.0))
            # rates per second
            lam_norm = float(self.cfg.get("spawn_rate_per_min", 0.1667)) / 60.0
            lam_surp = float(self.cfg.get("surprise_rate_per_min", 0.0556)) / 60.0
            # spawn probability in dt window: 1 - exp(-lambda * dt)
            import math as _m
            p_norm = 1.0 - _m.exp(-lam_norm * dt)
            p_surp = 1.0 - _m.exp(-lam_surp * dt)
            # First roll surprise (rare), else roll normal
            if self.rng.random() < p_surp:
                self._spawn_attempt(own_x, own_y, surprise=True)
            elif self.rng.random() < p_norm:
                self._spawn_attempt(own_x, own_y, surprise=False)
        except Exception:
            pass

        # motion
        for c in self.contacts:
            c.tick(dt_s, own_x, own_y)

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

        # priority + alarms
        self._select_priority(own_x, own_y)
        self._check_close_alarm(own_x, own_y)
        # morale effects: some Argentine aircraft abort when Harriers are close
        try:
            self._apply_harrier_deterrence()
        except Exception:
            pass

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

    def scan(self, own_x: float, own_y: float):
        # Scans are observational and decoupled from spawns (spawns are time-based in tick)
        if self.rec: self.rec.log("radar.scan", {"interval_s": self.cfg["scan_interval_s"]})

    # internals
    def _spawn_attempt(self, own_x: float, own_y: float, surprise: bool = False):
        if len(self.contacts) >= self.cfg["max_contacts"]:
            if self.rec: self.rec.log("radar.spawn_skip", {"reason": "max_contacts"})
            return

        if surprise:
            r_min, r_max = self.cfg["surprise_nm"], 14.0
        else:
            r0, _r1 = self.cfg["no_spawn_nm"]
            r_min, r_max = float(r0), float(self.cfg.get("offboard_max_nm", 30.0))

        r = self.rng.uniform(r_min, r_max)
        bearing_deg = self.rng.uniform(0.0, 360.0)
        rad = math.radians(bearing_deg)
        dx = math.sin(rad) * r
        dy = -math.cos(rad) * r
        x = max(0.0, min(float(WORLD_N), own_x + dx))
        y = max(0.0, min(float(WORLD_N), own_y + dy))

        # Decide allegiance: surprise always Hostile; otherwise Friendly with configured probability
        if surprise:
            allegiance = "Hostile"
        else:
            allegiance = ("Friendly" if (self.rng.random() < float(self.cfg.get("friendly_prob", 0.3))) else "Hostile")

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
            name, speed, klass = _pick_hostile()
            if surprise:
                tries = 0
                while name == 'Super Etendard' and tries < 10:
                    name, speed, klass = _pick_hostile()
                    tries += 1
            # Ensure Super Étendard starts at >= 20 nm (never uses 10 nm surprise)
            if name == 'Super Etendard' and r < 20.0:
                r = max(20.0, r)
            # Ensure hostile surface ships appear outside radar horizon (>= 20 nm)
            if klass and str(klass).lower() == 'ship' and r < 20.0:
                r = max(20.0, self.cfg.get("surface_spawn_min_nm", 20.0))

        course_deg = (bearing_deg + 180.0) % 360.0
        surface_meta: Optional[Dict[str, Any]] = None
        if str(allegiance).lower() == 'hostile' and str(klass or '').lower() == 'ship':
            surface_meta = {'hp': 4.0, 'max_hp': 4.0}
        meta = {
            "spawn": {"bearing_deg": round(bearing_deg,1), "range_nm": round(r,2), "surprise": surprise, "allegiance": allegiance},
            "cap": self.catalog.details(name)
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
            self.rec.log("radar.contact.new", {
                "id": c.id, "name": c.name, "allegiance": c.allegiance, "class": klass,
                "world_xy": [round(c.x,2), round(c.y,2)], "course_deg": c.course_deg,
                "speed_kts": c.speed_kts * HOSTILE_SPEED_SCALE
            })

    def force_spawn(self, own_x: float, own_y: float, allegiance: str, bearing_deg: float, range_nm: float) -> Contact:
        r = float(range_nm)
        rad = math.radians(float(bearing_deg))
        dx = math.sin(rad) * r
        dy = -math.cos(rad) * r
        x = max(0.0, min(float(WORLD_N), own_x + dx))
        y = max(0.0, min(float(WORLD_N), own_y + dy))
        if str(allegiance).title() == 'Friendly':
            name, speed, klass = self.catalog.pick_friendly()
            allegiance_norm = 'Friendly'
        else:
            name, speed, klass = self.catalog.pick_hostile()
            allegiance_norm = 'Hostile'
        course_deg = (float(bearing_deg) + 180.0) % 360.0
        surface_meta: Optional[Dict[str, Any]] = None
        if allegiance_norm == 'Hostile' and str(klass or '').lower() == 'ship':
            surface_meta = {'hp': 4.0, 'max_hp': 4.0}
        meta = {
            "spawn": {"bearing_deg": round(float(bearing_deg),1), "range_nm": round(r,2), "surprise": False, "forced": True},
            "cap": self.catalog.details(name)
        }
        if surface_meta is not None:
            meta['surface_ship'] = surface_meta
        c = Contact(
            id=self._next_id, name=name, allegiance=allegiance_norm,
            x=float(x), y=float(y), course_deg=course_deg, speed_kts=float(speed),
            threat="high" if name in ("Super Etendard", "Mirage III") else "medium",
            meta=meta
        )
        self._next_id += 1
        self.contacts.append(c)
        if self.rec:
            try:
                # include a display cell (board A..Z + 1..26) derived from world (y=row, x=col)
                def _clamp(v, lo, hi):
                    return lo if v < lo else hi if v > hi else v
                def _letters(i):
                    s=""; n=max(1,int(i));
                    while n>0:
                        n-=1; s=chr(ord('A')+(n%26))+s; n//=26
                    return s
                def _world_to_cell(row, col):
                    def mv(v):
                        t=1.0+(_clamp(v,0.0,float(WORLD_N))*(BOARD_N-1)/float(WORLD_N))
                        return int(round(_clamp(t,1.0,float(BOARD_N))))
                    r_i, c_i = mv(row), mv(col)
                    return f"{_letters(c_i)}{r_i}"
                cell = _world_to_cell(y, x)
                self.rec.log("radar.force_spawn", {
                    "bearing_deg": round(float(bearing_deg),1),
                    "range_nm": round(r,2),
                    "chosen": {"name": name, "speed_kts": speed, "allegiance": allegiance_norm},
                    "target_world_xy": [round(x,2), round(y,2)],
                    "ship_world_xy": [round(own_x,2), round(own_y,2)],
                    "cell": cell,
                })
            except Exception:
                pass
        return c

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
        cell = str(st.get('origin_cell') or st.get('cell') or '')
        if not cell:
            return
        try:
            from projects.falklandV2.grid.mapping import label_to_world  # late import
            x, y = label_to_world(cell, world_n=float(WORLD_N))
        except Exception:
            return
        if self._resupply_contact_id is not None:
            c = next((k for k in self.contacts if int(getattr(k,'id',-1)) == int(self._resupply_contact_id)), None)
            if c is not None:
                c.x = float(x); c.y = float(y)
                return
        # Create contact
        cid = int(self._next_id); self._next_id += 1
        meta = {'resupply': True, 'stage': stage}
        c = Contact(id=cid, name='Sea King Helicopter', allegiance='Friendly', x=float(x), y=float(y), course_deg=0.0, speed_kts=90.0, threat='low', meta=meta)
        self.contacts.append(c)
        self._resupply_contact_id = cid

    # ---- CAP contact injection --------------------------------------------
    @staticmethod
    def _cell_to_world(cell: str) -> Tuple[float, float]:
        # Map grid cell like 'K13' to approximate world (x,y) inside 40x40 world
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
        # World min offset so that A1 maps to (BOARD_MIN, BOARD_MIN)
        board_min = (float(WORLD_N) - float(BOARD_N)) / 2.0
        x = board_min + float(col_idx)
        y = board_min + float(row_idx)
        return (x, y)

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
        want: Dict[int, Tuple[float, float, str]] = {}
        for m in missions:
            try:
                status = str(m.get('status') or '').lower()
                if status not in ACTIVE:
                    continue
                mid = int(m.get('id'))
                # Choose placement by status: queued/rtb/recovering at origin; onstation/airborne at target
                if status in ("queued", "rtb", "recovering"):
                    cell = str(m.get('origin_cell') or m.get('cur_cell') or m.get('target_cell') or '').strip()
                else:
                    cell = str(m.get('cur_cell') or m.get('target_cell') or m.get('origin_cell') or '').strip()
                if not cell:
                    continue
                x, y = self._cell_to_world(cell)
                want[mid] = (x, y, cell)
            except Exception:
                continue

        # Remove stale CAP contacts
        keep_ids: set[int] = set()
        for mid, cid in list(self._cap_contacts.items()):
            if mid not in want:
                # Drop contact with id=cid if present
                self.contacts = [c for c in self.contacts if int(getattr(c, 'id', -1)) != int(cid)]
                self._cap_contacts.pop(mid, None)
            else:
                keep_ids.add(mid)

        # Ensure/update required CAP contacts
        # Resolve catalog details (capability + speed)
        cap_name = 'Sea Harrier FRS.1'
        # Try to find a speed value for the CAP type
        try:
            # Probe friendly pool to find matching speed
            # Note: Catalog intentionally excludes Harrier from friendlies; fall back to 420
            speed_kts = float(self.catalog._details.get(cap_name, {}).get('speed_kts') or 420.0)
        except Exception:
            speed_kts = 420.0
        for mid, (x, y, cell) in want.items():
            cid = self._cap_contacts.get(mid)
            if cid is not None:
                # Update existing
                c = next((k for k in self.contacts if int(getattr(k, 'id', -1)) == int(cid)), None)
                if c is not None:
                    c.x = float(x)
                    c.y = float(y)
                    # keep other fields as-is
                    continue
                else:
                    # stale index entry; remove and re-create
                    self._cap_contacts.pop(mid, None)

            # Create a new friendly CAP contact
            cid = int(self._next_id)
            self._next_id += 1
            meta = {
                'cap_flight': True,
                'mission_id': mid,
                'station_cell': cell,
                'kind': 'cap',
                'cap': self.catalog.details(cap_name),
            }
            c = Contact(
                id=cid,
                name=cap_name,
                allegiance='Friendly',
                x=float(x), y=float(y),
                course_deg=0.0,
                speed_kts=float(speed_kts),
                threat='low',
                meta=meta,
            )
            self.contacts.append(c)
            self._cap_contacts[mid] = cid

    def _apply_harrier_deterrence(self) -> None:
        harriers = [c for c in self.contacts if str(getattr(c, 'allegiance', '')) == 'Friendly' and bool(getattr(c, 'meta', {}).get('cap_flight'))]
        if not harriers:
            return
        now_ts = time.time()
        for hostile in self.contacts:
            try:
                if str(getattr(hostile, 'allegiance', '')) != 'Hostile':
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
            except Exception:
                continue

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
