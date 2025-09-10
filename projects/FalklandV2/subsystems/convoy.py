# subsystems/convoy.py
"""
Convoy subsystem (stable)
- Reads escorts from data/convoy.json
- Keeps escorts in formation with the leader (own ship)
- Applies a 30s "order lag" before escorts adopt new course/speed
- Returns escort snapshots for HUD / radar lists

Public surface
- Convoy.load(data_path) -> Convoy
- convoy.update(own_x, own_y, course_deg, speed_kts, grid) -> List[EscortSnap]
- convoy.hud_fragment(escorts) -> "ESCORTS: Hermes=I11, Glamorgan=L14"
"""

from __future__ import annotations
import json, math, time, random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from subsystems import nav as navi
from subsystems import contacts as cons

DELAY_S = 30  # adopt new course/speed after this delay

@dataclass
class EscortDef:
    id: str
    name: str
    klass: str
    type: str
    allegiance: str
    offset_cells: Tuple[float, float]
    speed_lock: str
    role: List[str]

@dataclass
class EscortSnap:
    id: str
    name: str
    klass: str
    type: str
    allegiance: str
    x: float
    y: float
    cell: str
    course_deg: float
    speed_kts: float

class Convoy:
    def __init__(self, escorts: List[EscortDef]):
        self._escorts = escorts
        self._last_course: float = 0.0
        self._last_speed: float = 0.0
        self._last_set: float = 0.0
        self._delay_s: float = 30.0
        self._init = False

    @classmethod
    def load(cls, data_path: Path) -> "Convoy":
        cfg_path = data_path / "convoy.json"
        if not cfg_path.exists():
            return cls([])
        try:
            doc = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            return cls([])
        escs: List[EscortDef] = []
        for e in doc.get("escorts", []):
            escs.append(EscortDef(
                id=str(e.get("id", "esc")),
                name=str(e.get("name", "Escort")),
                klass=str(e.get("class", "")),
                type=str(e.get("type", "ship")),
                allegiance=str(e.get("allegiance", "Friendly")),
                offset_cells=tuple(e.get("offset_cells", [0, 0]))[:2],  # type: ignore
                speed_lock=str(e.get("speed_lock", "leader")),
                role=list(e.get("role", [])),
            ))
        return cls(escs)

    @staticmethod
    def _rotate_offset(dx: float, dy: float, course_deg: float) -> Tuple[float, float]:
        rad = math.radians(course_deg)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        rx = dx * cos_a - dy * sin_a
        ry = dx * sin_a + dy * cos_a
        return rx, ry

    def _lagged_course_speed(self, course_deg: float, speed_kts: float) -> Tuple[float, float]:
        now = time.time()
        if not self._init:
            self._last_course = course_deg
            self._last_speed = speed_kts
            self._last_set = now
            self._delay_s = random.uniform(30.0, 50.0)
            self._init = True
            return self._last_course, self._last_speed

        changed = (abs((course_deg - self._last_course) % 360.0) > 0.1) or (abs(speed_kts - self._last_speed) > 0.1)
        if changed and (now - self._last_set) >= self._delay_s:
            self._last_course = course_deg % 360.0
            self._last_speed = max(0.0, speed_kts)
            self._last_set = now
            self._delay_s = random.uniform(30.0, 50.0)

        return self._last_course, self._last_speed

    def update(self,
               own_x: float,
               own_y: float,
               course_deg: float,
               speed_kts: float,
               grid: Any) -> List[EscortSnap]:
        out: List[EscortSnap] = []
        eff_course, eff_speed = self._lagged_course_speed(course_deg, speed_kts)
        for e in self._escorts:
            odx, ody = float(e.offset_cells[0]), float(e.offset_cells[1])
            rdx, rdy = self._rotate_offset(odx, ody, eff_course)
            ex = own_x + rdx
            ey = own_y + rdy
            cx = int(round(ex))
            cy = int(round(ey))
            cell = cons.format_cell(cx, cy) if hasattr(cons, "format_cell") else navi.format_cell(cx, cy)
            out.append(EscortSnap(
                id=e.id, name=e.name, klass=e.klass, type=e.type, allegiance=e.allegiance,
                x=ex, y=ey, cell=cell, course_deg=eff_course, speed_kts=eff_speed
            ))
        return out

    @staticmethod
    def hud_fragment(escorts: List[EscortSnap]) -> str:
        if not escorts:
            return "ESCORTS: —"
        pieces = [f"{e.name.split()[1] if e.name.startswith('HMS ') else e.name}={e.cell}" for e in escorts]
        return "ESCORTS: " + ", ".join(pieces)
