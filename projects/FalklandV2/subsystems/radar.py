#!/usr/bin/env python3
"""
Radar subsystem (Step 7) — lock/unlock + target selection + status text.

Pure helpers (no file I/O, no prints in API). Designed to be called by the engine.

API (stable):
- choose_primary(pool, ship_xy, mode="nearest_hostile") -> contact_id | None
- lock_contact(state_dict, contact_id) -> None          # stores under state["radar"]["locked_contact_id"]
- unlock_contact(state_dict) -> None
- status_line(pool, ship_xy, locked_id=None, max_list=3) -> str

This module *assumes* contacts.ContactPool and its Contact class (from subsystems/contacts.py).
It does not import engine. The demo at bottom shows standalone behavior with a tiny mock.
"""

from __future__ import annotations
from typing import Optional, Tuple, List
import math

# We don't import contacts here to keep this module pure; the engine will pass in the pool.

def _range_nm(ax: float, ay: float, bx: float, by: float, cell_nm: float) -> float:
    return math.hypot(bx - ax, by - ay) * cell_nm

def choose_primary(pool, ship_xy: Tuple[float, float], mode: str = "nearest_hostile") -> Optional[int]:
    """
    Policy hook to pick a primary target.
    mode:
      - "nearest_hostile": nearest contact with allegiance.lower()=="hostile"
      - "nearest_any": nearest contact of any allegiance (fallback when no hostiles)
    Returns contact id or None.
    """
    sx, sy = ship_xy
    cell_nm = pool.grid.cell_nm

    hostiles = [c for c in pool.contacts if c.allegiance.lower() == "hostile"]
    if mode == "nearest_hostile":
        seq = hostiles
    elif mode == "nearest_any":
        seq = list(pool.contacts)
    else:
        seq = hostiles or list(pool.contacts)

    if not seq:
        return None

    best = min(seq, key=lambda c: _range_nm(c.x, c.y, sx, sy, cell_nm))
    return best.id

def lock_contact(state: dict, contact_id: Optional[int]) -> None:
    """Persist lock in state dict under state['radar']['locked_contact_id']."""
    radar = state.setdefault("radar", {})
    radar["locked_contact_id"] = int(contact_id) if contact_id is not None else None

def unlock_contact(state: dict) -> None:
    radar = state.setdefault("radar", {})
    radar["locked_contact_id"] = None

def status_line(pool, ship_xy: Tuple[float, float], locked_id: Optional[int] = None, max_list: int = 3) -> str:
    """
    Produce a terse human string: "<n> contacts | locked: #ID Name d=nm | nearest: #ID Name d=nm ..."
    """
    sx, sy = ship_xy
    n = len(pool.contacts)
    if n == 0:
        return "RADAR: no contacts."

    # compute ranges
    infos = []
    for c in pool.contacts:
        d = _range_nm(c.x, c.y, sx, sy, pool.grid.cell_nm)
        infos.append((d, c))

    infos.sort(key=lambda t: t[0])
    parts: List[str] = [f"RADAR: {n} contact(s)"]

    # locked target
    if locked_id is not None:
        locked = next((c for _, c in infos if c.id == locked_id), None)
        if locked:
            parts.append(f"locked #{locked.id} {locked.name} {locked.allegiance} d={infos[[c.id for _, c in infos].index(locked.id)][0]:.1f}nm")

    # nearest few
    top = infos[:max_list]
    nearest_bits = [f"#{c.id} {c.name} {c.allegiance} d={d:.1f}nm" for d, c in top]
    parts.append("nearest: " + " | ".join(nearest_bits))

    return " | ".join(parts)

# ---------- Minimal demo (standalone) ----------
# Safe to run without touching engine; we fake a tiny pool.

if __name__ == "__main__":
    from dataclasses import dataclass
    @dataclass
    class Grid: cols:int=26; rows:int=26; cell_nm:float=1.0
    @dataclass
    class C: id:int; name:str; allegiance:str; x:float; y:float
    class Pool:
        def __init__(self): self.grid=Grid(); self.contacts=[]
    p = Pool()
    # Fake three contacts around ship at N13 (x=13, y=12)
    p.contacts = [
        C(1,"A-4 Skyhawk","Hostile", 8.0, 10.0),
        C(2,"Type 22 Frigate","Friendly", 20.0, 18.0),
        C(3,"Dagger","Hostile", 14.0, 12.0),
    ]
    ship_xy = (13.0, 12.0)
    pick = choose_primary(p, ship_xy, "nearest_hostile")
    s = {}
    lock_contact(s, pick)
    print(status_line(p, ship_xy, s["radar"]["locked_contact_id"]))
    unlock_contact(s)
    print(status_line(p, ship_xy, None))