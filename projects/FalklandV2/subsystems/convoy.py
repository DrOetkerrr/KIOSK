# subsystems/convoy.py
"""
Convoy subsystem
- Reads escorts from data/convoy.json
- Keeps Hermes + Type 21 (or any escorts) “in formation” with own ship
- Formation is expressed as cell offsets relative to the ship’s heading
- Returns escort snapshots you can surface in HUD and radar lists

Coordinate notes
- Grid uses lettered columns (A..Z) and numbered rows (1..N). We work in the
  same float XY space as contacts: x → columns (letters), y → rows (numbers).
- Positive dx means 'east' when course=0°. We rotate offsets by current course.
- Because rotation yields fractional cells, we round to nearest cell.

Public surface
- Convoy.load(data_path) -> Convoy
- convoy.update(own_x, own_y, course_deg, speed_kts, grid) -> List[dict]
- convoy.hud_fragment(escorts) -> short text for HUD like: "ESCORTS: Hermes=I11, T21=L14"
"""

from __future__ import annotations
import json, math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# local helpers from existing subsystems
from subsystems import nav as navi
from subsystems import contacts as cons

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

    # ---------- loading ----------
    @classmethod
    def load(cls, data_path: Path) -> "Convoy":
        cfg_path = data_path / "convoy.json"
        if not cfg_path.exists():
            # default: no escorts
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

    # ---------- math helpers ----------
    @staticmethod
    def _rotate_offset(dx: float, dy: float, course_deg: float) -> Tuple[float, float]:
        """Rotate an offset in *cell units* around the leader by course (0°=east)."""
        rad = math.radians(course_deg)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        rx = dx * cos_a - dy * sin_a
        ry = dx * sin_a + dy * cos_a
        return rx, ry

    # ---------- core ----------
    def update(self,
               own_x: float,
               own_y: float,
               course_deg: float,
               speed_kts: float,
               grid: Any) -> List[EscortSnap]:
        """
        Compute current escort positions as they maintain formation around the leader.
        Returns a list of EscortSnap (with x,y and cell strings).
        """
        out: List[EscortSnap] = []
        for e in self._escorts:
            odx, ody = float(e.offset_cells[0]), float(e.offset_cells[1])
            rdx, rdy = self._rotate_offset(odx, ody, course_deg)
            ex = own_x + rdx
            ey = own_y + rdy
            # snap to nearest grid cell for display/radar
            cx = int(round(ex))
            cy = int(round(ey))
            cell = cons.format_cell(cx, cy) if hasattr(cons, "format_cell") else navi.format_cell(cx, cy)
            out.append(EscortSnap(
                id=e.id, name=e.name, klass=e.klass, type=e.type, allegiance=e.allegiance,
                x=ex, y=ey, cell=cell, course_deg=course_deg, speed_kts=speed_kts
            ))
        return out

    # ---------- HUD helper ----------
    @staticmethod
    def hud_fragment(escorts: List[EscortSnap]) -> str:
        if not escorts:
            return "ESCORTS: —"
        pieces = [f"{e.name.split()[1] if e.name.startswith('HMS ') else e.name}={e.cell}" for e in escorts]
        return "ESCORTS: " + ", ".join(pieces)