#!/usr/bin/env python3
"""
Radar subsystem — lock/unlock + target selection + status text.

Formatting updated: every reported contact snippet now starts with
CELL → NAME/TYPE → DISTANCE → other info.
"""

from __future__ import annotations
from typing import Optional, Tuple, List
import math

def _range_nm(ax: float, ay: float, bx: float, by: float, cell_nm: float) -> float:
    return math.hypot(bx - ax, by - ay) * cell_nm

def choose_primary(pool, ship_xy: Tuple[float, float], mode: str = "nearest_hostile") -> Optional[int]:
    sx, sy = ship_xy
    cell_nm = pool.grid.cell_nm
    hostiles = [c for c in pool.contacts if c.allegiance.lower() == "hostile"]
    seq = hostiles if mode == "nearest_hostile" else list(pool.contacts)
    if not seq:
        return None
    best = min(seq, key=lambda c: _range_nm(c.x, c.y, sx, sy, cell_nm))
    return best.id

def lock_contact(state: dict, contact_id: Optional[int]) -> None:
    radar = state.setdefault("radar", {})
    radar["locked_contact_id"] = int(contact_id) if contact_id is not None else None

def unlock_contact(state: dict) -> None:
    radar = state.setdefault("radar", {})
    radar["locked_contact_id"] = None

def _cell_for(c) -> str:
    # Present as grid cell (rounded to nearest) e.g., "N13"
    x = max(0, min(pool_grid_cols(c)-1, int(round(c.x))))
    y = max(0, min(pool_grid_rows(c)-1, int(round(c.y))))
    return f"{chr(ord('A') + x)}{y+1}"

def pool_grid_cols(c) -> int:
    return getattr(getattr(c, "__dict__", {}).get("_grid_override", None), "cols", None) or c.__class__.__dict__.get("_grid_cols", None) or 26

def pool_grid_rows(c) -> int:
    return getattr(getattr(c, "__dict__", {}).get("_grid_override", None), "rows", None) or c.__class__.__dict__.get("_grid_rows", None) or 26

def status_line(pool, ship_xy: Tuple[float, float], locked_id: Optional[int] = None, max_list: int = 3) -> str:
    """
    RADAR line with cell-first formatting:
      RADAR: <n> contact(s) | locked: <CELL> <NAME> <ALLEGIANCE> d=<nm> | nearest: <CELL> <NAME> <ALLEGIANCE> d=<nm> | ...
    """
    sx, sy = ship_xy
    n = len(pool.contacts)
    if n == 0:
        return "RADAR: no contacts."

    # compute ranges
    infos = []
    for c in pool.contacts:
        d = _range_nm(c.x, c.y, sx, sy, pool.grid.cell_nm)
        cell = f"{chr(ord('A') + int(round(c.x)))}{int(round(c.y))+1}"
        infos.append((d, cell, c))

    infos.sort(key=lambda t: t[0])
    parts: List[str] = [f"RADAR: {n} contact(s)"]

    # locked target, if present
    if locked_id is not None:
        locked = next((t for t in infos if t[2].id == locked_id), None)
        if locked:
            d, cell, c = locked
            parts.append(f"locked: {cell} {c.name} {c.allegiance} d={d:.1f}nm (#{c.id})")

    # nearest few
    top = infos[:max_list]
    nearest_bits = [f"{cell} {c.name} {c.allegiance} d={d:.1f}nm (#{c.id})" for d, cell, c in top]
    parts.append("nearest: " + " | ".join(nearest_bits))

    return " | ".join(parts)

# ---------- Minimal demo ----------
if __name__ == "__main__":
    from dataclasses import dataclass
    @dataclass
    class Grid: cols:int=26; rows:int=26; cell_nm:float=1.0
    @dataclass
    class C: id:int; name:str; allegiance:str; x:float; y:float
    class Pool:
        def __init__(self): self.grid=Grid(); self.contacts=[]
    p = Pool()
    # Fake contacts around ship at N13 (13,12)
    p.contacts = [
        C(1,"A-4 Skyhawk","Hostile", 8.0, 10.0),
        C(2,"Type 22 Frigate","Friendly", 20.0, 18.0),
        C(3,"Dagger","Hostile", 14.0, 12.0),
    ]
    ship_xy = (13.0, 12.0)
    print(status_line(p, ship_xy, locked_id=3))
    print(status_line(p, ship_xy, locked_id=None))