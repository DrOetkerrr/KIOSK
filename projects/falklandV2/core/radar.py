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

from __future__ import annotations
import math, random, time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Set

# --- World constants (match engine) ------------------------------------------
WORLD_N = 40
BOARD_N = 26

def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v

def nm_distance(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(bx - ax, by - ay)

# --- Minimal hostile table (subset; later move to rules) ---------------------
HOSTILES = [
    # name, speed_kts, weight
    ("A-4 Skyhawk", 385, 5),
    ("Dagger (Mirage V)", 420, 4),
    ("Mirage III", 455, 3),
    ("Pucara", 196, 2),
    ("Super Etendard", 434, 1),
    ("Canberra bomber", 336, 1),
]
HOSTILE_SPEED_SCALE = 0.75  # move at 75% of real speed

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
        if self.allegiance == "Hostile":
            # gentle steering toward own ship
            desired = math.degrees(math.atan2(own_x - self.x, -(own_y - self.y))) % 360.0
            turn = (desired - self.course_deg + 540) % 360 - 180
            max_turn_per_s = 5.0 / 60.0  # 5°/min
            turn_clamped = clamp(turn, -max_turn_per_s * dt_s, max_turn_per_s * dt_s)
            self.course_deg = (self.course_deg + turn_clamped) % 360.0

        if self.speed_kts > 0:
            nm = (self.speed_kts * HOSTILE_SPEED_SCALE) * (dt_s / 3600.0)
            rad = math.radians(self.course_deg)
            dx = math.sin(rad) * nm
            dy = -math.cos(rad) * nm
            self.x = clamp(self.x + dx, 0.0, float(WORLD_N))
            self.y = clamp(self.y + dy, 0.0, float(WORLD_N))

# --- Radar -------------------------------------------------------------------
class Radar:
    def __init__(self, rec=None, cfg: Optional[dict] = None, rng: Optional[random.Random] = None):
        self.rec = rec
        self.rng = rng or random.Random()
        self.cfg = {
            "scan_interval_s": 360,
            "no_spawn_nm": [15.0, 20.0],
            "surprise_nm": 10.0,
            "offboard_max_nm": 40.0,
            "max_contacts": 10,
            "close_threat_nm": 3.0,
            "close_alarm_cooldown_s": 30.0,
        }
        if cfg: self.cfg.update(cfg)
        self.contacts: List[Contact] = []
        self._accum = 0.0
        self._pending_detection: Set[int] = set()
        self._next_id = 1
        self.priority_id: Optional[int] = None
        self._manual_lock = False

    # API
    def tick(self, dt_s: float, own_x: float, own_y: float):
        # cadence
        self._accum += dt_s
        if self._accum >= self.cfg["scan_interval_s"]:
            self._accum = 0.0
            self.scan(own_x, own_y)

        # motion
        for c in self.contacts:
            c.tick(dt_s, own_x, own_y)

        # priority + alarms
        self._select_priority(own_x, own_y)
        self._check_close_alarm(own_x, own_y)

        # cap count
        if len(self.contacts) > self.cfg["max_contacts"]:
            self.contacts = self.contacts[: self.cfg["max_contacts"]]

    def scan(self, own_x: float, own_y: float):
        if self.rec:
            self.rec.log("radar.scan", {"interval_s": self.cfg["scan_interval_s"]})
        self._flush_pending_detections()
        roll = self.rng.randint(1, 6)
        surprise = (roll == 1)
        if roll >= 5 or surprise:
            self._spawn_attempt(own_x, own_y, surprise=surprise)

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

    # internals
    def _spawn_attempt(self, own_x: float, own_y: float, surprise: bool = False):
        if len(self.contacts) >= self.cfg["max_contacts"]:
            if self.rec: self.rec.log("radar.spawn_skip", {"reason": "max_contacts"})
            return

        if surprise:
            r_min, r_max = self.cfg["surprise_nm"], 14.0
        else:
            r0, _r1 = self.cfg["no_spawn_nm"]
            r_min, r_max = float(r0), float(self.cfg["offboard_max_nm"])

        r = self.rng.uniform(r_min, r_max)
        bearing_deg = self.rng.uniform(0.0, 360.0)
        rad = math.radians(bearing_deg)
        dx = math.sin(rad) * r
        dy = -math.cos(rad) * r
        x = max(0.0, min(float(WORLD_N), own_x + dx))
        y = max(0.0, min(float(WORLD_N), own_y + dy))

        # weighted hostile pick
        total_w = float(sum(w for _, _, w in HOSTILES))
        pick = self.rng.uniform(0.0, total_w)
        upto = 0.0
        name, speed = HOSTILES[0][0], HOSTILES[0][1]
        for n, s, w in HOSTILES:
            if upto + w >= pick:
                name, speed = n, s
                break
            upto += w

        course_deg = (bearing_deg + 180.0) % 360.0
        c = Contact(
            id=self._next_id, name=name, allegiance="Hostile",
            x=x, y=y, course_deg=course_deg, speed_kts=float(speed),
            threat="high" if name in ("Super Etendard", "Mirage III") else "medium",
            meta={
                "spawn": {"bearing_deg": round(bearing_deg,1), "range_nm": round(r,2), "surprise": surprise},
                "class": name,
            }
        )
        self._next_id += 1
        self.contacts.append(c)

        if self.rec:
            self.rec.log("radar.spawn_attempt", {
                "bearing_deg": round(bearing_deg, 1),
                "range_nm": round(r, 2),
                "surprise": surprise,
                "chosen": {"name": name, "speed_kts": speed},
                "target_world_xy": [round(x, 2), round(y, 2)],
                "ship_world_xy": [round(own_x, 2), round(own_y, 2)],
                "policy": {
                    "no_spawn_nm": self.cfg["no_spawn_nm"],
                    "surprise_nm": self.cfg["surprise_nm"],
                    "offboard_max_nm": self.cfg["offboard_max_nm"],
                    "max_contacts": self.cfg["max_contacts"]
                }
            })
        self._pending_detection.add(c.id)

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

    def _select_priority(self, own_x: float, own_y: float):
        if self._manual_lock:
            try:
                pid = int(self.priority_id) if self.priority_id is not None else None
            except Exception:
                pid = None
            if pid is not None:
                current = next((c for c in self.contacts if int(getattr(c, 'id', -1)) == pid), None)
                if current is not None:
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
