# subsystems/convoy.py
"""
Convoy subsystem (stable)
- Reads escorts from data/convoy.json
- Keeps escorts in formation with the leader (own ship)
- Smoothly converges escort course/speed toward the leader (no instant jumps)
- Returns escort snapshots for HUD / radar lists

Public surface
- Convoy.load(data_path) -> Convoy
- convoy.update(own_x, own_y, course_deg, speed_kts, grid) -> List[EscortSnap]
- convoy.hud_fragment(escorts) -> "ESCORTS: Hermes=I11, Glamorgan=L14"
"""

from __future__ import annotations
import json, math, time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from projects.falklandV2.subsystems import nav as navi
from projects.falklandV2.subsystems import contacts as cons

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
        self._offsets_base = {e.id: (float(e.offset_cells[0]), float(e.offset_cells[1])) for e in escorts}
        self._offsets = dict(self._offsets_base)
        self._current_course: float = 0.0
        self._current_speed: float = 0.0
        self._target_course: float = 0.0
        self._target_speed: float = 0.0
        self._last_update_ts: float | None = None
        self._max_course_rate: float = 4.0   # degrees per second (slow turn for large vessels)
        self._max_speed_rate: float = 1.5    # knots per second acceleration/deceleration
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

    @staticmethod
    def _normalize_course(course_deg: float) -> float:
        return float(course_deg) % 360.0

    @staticmethod
    def _step_toward_angle(current: float, target: float, max_step: float) -> float:
        if max_step <= 0.0:
            return Convoy._normalize_course(current)
        diff = ((target - current + 180.0) % 360.0) - 180.0
        if abs(diff) <= max_step:
            return Convoy._normalize_course(target)
        step = math.copysign(min(abs(diff), max_step), diff)
        return Convoy._normalize_course(current + step)

    @staticmethod
    def _step_toward_scalar(current: float, target: float, max_step: float) -> float:
        if max_step <= 0.0:
            return float(current)
        diff = target - current
        if abs(diff) <= max_step:
            return float(target)
        return float(current + math.copysign(max_step, diff))

    def _smoothed_course_speed(self, course_deg: float, speed_kts: float) -> Tuple[float, float]:
        now = time.time()
        if not self._init:
            self._current_course = self._normalize_course(course_deg)
            self._current_speed = max(0.0, float(speed_kts))
            self._target_course = self._current_course
            self._target_speed = self._current_speed
            self._last_update_ts = now
            self._init = True
            return self._current_course, self._current_speed

        self._target_course = self._normalize_course(course_deg)
        self._target_speed = max(0.0, float(speed_kts))

        last_ts = self._last_update_ts or now
        dt = max(0.0, float(now - last_ts))
        self._last_update_ts = now

        course_step = self._max_course_rate * dt
        speed_step = self._max_speed_rate * dt

        self._current_course = self._step_toward_angle(self._current_course, self._target_course, course_step)
        self._current_speed = self._step_toward_scalar(self._current_speed, self._target_speed, speed_step)

        return self._current_course, self._current_speed

    def update(self,
               own_x: float,
               own_y: float,
               course_deg: float,
               speed_kts: float,
               grid: Any) -> List[EscortSnap]:
        out: List[EscortSnap] = []
        eff_course, eff_speed = self._smoothed_course_speed(course_deg, speed_kts)
        for e in self._escorts:
            offset = self._offsets.get(e.id) or self._offsets_base.get(e.id) or (0.0, 0.0)
            odx, ody = float(offset[0]), float(offset[1])
            rdx, rdy = self._rotate_offset(odx, ody, eff_course)
            ex = own_x + rdx
            ey = own_y + rdy
            try:
                from . import webcore
                cell = webcore.cell_for_world(ey, ex)
            except Exception:
                cx = int(round(ex))
                cy = int(round(ey))
                cell = cons.format_cell(cx, cy) if hasattr(cons, "format_cell") else navi.format_cell(cx, cy)
            out.append(EscortSnap(
                id=e.id, name=e.name, klass=e.klass, type=e.type, allegiance=e.allegiance,
                x=ex, y=ey, cell=cell, course_deg=eff_course, speed_kts=eff_speed
            ))
        return out

    def get_offset(self, escort_id: str) -> Tuple[float, float] | None:
        if escort_id in self._offsets:
            return self._offsets[escort_id]
        if escort_id in self._offsets_base:
            return self._offsets_base[escort_id]
        return None

    def distance_cells(self, escort_id: str) -> float:
        offset = self.get_offset(escort_id)
        if not offset:
            return 0.0
        return math.hypot(float(offset[0]), float(offset[1]))

    def adjust_distance(self, escort_id: str, delta_cells: float, *, min_cells: float = 1.0, max_cells: float = 8.0) -> Tuple[float, bool]:
        base = self.get_offset(escort_id)
        if base is None:
            return 0.0, False
        dx, dy = float(base[0]), float(base[1])
        length = math.hypot(dx, dy)
        if length < 1e-6:
            dx, dy = 0.0, -1.0
            length = 1.0
        new_length = max(min_cells, min(max_cells, length + float(delta_cells)))
        changed = abs(new_length - length) > 1e-6
        scale = new_length / length if length > 1e-6 else 1.0
        self._offsets[escort_id] = (dx * scale, dy * scale)
        return new_length, changed

    def escort_world_position(self, escort_id: str, own_x: float, own_y: float, course_deg: float) -> Tuple[float, float]:
        offset = self.get_offset(escort_id)
        if not offset:
            return own_x, own_y
        rdx, rdy = self._rotate_offset(float(offset[0]), float(offset[1]), course_deg)
        return own_x + rdx, own_y + rdy

    def escort_world_cell(self, escort_id: str, own_x: float, own_y: float, course_deg: float) -> Tuple[float, float, str]:
        wx, wy = self.escort_world_position(escort_id, own_x, own_y, course_deg)
        try:
            from . import webcore
            cell = webcore.cell_for_world(wy, wx)
        except Exception:
            try:
                cell = cons.format_cell(int(round(wx)), int(round(wy)))
            except Exception:
                cell = 'K13'
        return wx, wy, cell

    @staticmethod
    def hud_fragment(escorts: List[EscortSnap]) -> str:
        if not escorts:
            return "ESCORTS: —"
        pieces = [f"{e.name.split()[1] if e.name.startswith('HMS ') else e.name}={e.cell}" for e in escorts]
        return "ESCORTS: " + ", ".join(pieces)
