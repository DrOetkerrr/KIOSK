#!/usr/bin/env python3
"""
Navigation subsystem (Step 3) — pure functions only.

Contract (stable):
- Grid is 26x26 cells, columns A–Z (west→east), rows 1–26 (south→north).
- Each cell is 1.0 nautical mile square (configurable).
- Course convention: 0° = North, 90° = East, 180° = South, 270° = West.
- Speed in knots (nm/hour). Distance moved = (speed_kts * dt_seconds / 3600).

This module does NOT touch global state or files.
Engine will call:
  - cell_to_xy / xy_to_cell
  - parse_cell / format_cell
  - step_position() to advance continuous XY by dt
  - border_alert() to warn when 1 cell before leaving grid on current course

We represent ship position in continuous grid units (x,y floats) where:
  x = 0..25  → A..Z (west→east)
  y = 0..25  → 1..26 (south→north)
Cell centers sit at integer coordinates (e.g., K13 is (10,12) in 0-based XY).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple, Optional
import math
import re

# ---------- Grid helpers

def col_to_x(col: str) -> int:
    """A→0, B→1, ..., Z→25"""
    c = col.strip().upper()
    if not (len(c) == 1 and 'A' <= c <= 'Z'):  # defensive
        raise ValueError(f"Invalid column letter: {col}")
    return ord(c) - ord('A')

def x_to_col(x: int) -> str:
    if not (0 <= x <= 25):
        raise ValueError(f"Invalid x for column: {x}")
    return chr(ord('A') + x)

def row_to_y(row: int) -> int:
    """1→0, 2→1, ..., 26→25 (south→north)"""
    if not (1 <= row <= 26):
        raise ValueError(f"Invalid row number: {row}")
    return row - 1

def y_to_row(y: int) -> int:
    if not (0 <= y <= 25):
        raise ValueError(f"Invalid y for row: {y}")
    return y + 1

_cell_re = re.compile(r"^([A-Za-z])\s*([1-2]?[0-9]|26)$")

def parse_cell(cell: str) -> Tuple[int, int]:
    """
    'K13' → (x=10, y=12)  [0-based]
    """
    m = _cell_re.match(cell.strip())
    if not m:
        raise ValueError(f"Bad cell '{cell}'")
    col, row = m.group(1), int(m.group(2))
    return col_to_x(col), row_to_y(row)

def format_cell(x: int, y: int) -> str:
    return f"{x_to_col(x)}{y_to_row(y)}"

def cell_to_xy(cell: str) -> Tuple[int, int]:
    return parse_cell(cell)

def xy_to_cell(x: int, y: int) -> str:
    return format_cell(x, y)

# ---------- Motion

@dataclass(frozen=True)
class GridCfg:
    cols: int = 26
    rows: int = 26
    cell_nm: float = 1.0  # nautical miles per cell

@dataclass
class NavState:
    """Continuous XY position in grid units (floats)."""
    x: float
    y: float

def nm_per_dt(speed_kts: float, dt_seconds: float) -> float:
    return speed_kts * (dt_seconds / 3600.0)

def step_position(
    pos: NavState,
    course_deg: float,
    speed_kts: float,
    dt_seconds: float,
    grid: GridCfg = GridCfg()
) -> NavState:
    """
    Advance continuous position by dt, clamped to grid bounds.
    Returns new NavState (mutates nothing).
    """
    # Distance in nautical miles during dt
    d_nm = nm_per_dt(speed_kts, dt_seconds)
    if d_nm <= 0.0:
        return NavState(pos.x, pos.y)

    # Convert nm to grid units
    d_cells = d_nm / max(1e-9, grid.cell_nm)

    # Bearing: 0°=N (positive y), 90°=E (positive x)
    rad = math.radians(course_deg % 360.0)
    dx = d_cells * math.sin(rad)
    dy = d_cells * math.cos(rad)

    nx = pos.x + dx
    ny = pos.y + dy

    # Clamp to grid bounds (stay inside 0..cols-1/rows-1)
    nx = max(0.0, min(grid.cols - 1e-6, nx))
    ny = max(0.0, min(grid.rows - 1e-6, ny))

    return NavState(nx, ny)

def snapped_cell(pos: NavState) -> Tuple[int, int]:
    """
    Convert continuous pos to the containing cell (nearest integer index).
    We round to nearest integer center; ties floor.
    """
    x_idx = int(round(pos.x))
    y_idx = int(round(pos.y))
    # clamp safe
    x_idx = max(0, min(25, x_idx))
    y_idx = max(0, min(25, y_idx))
    return x_idx, y_idx

def border_alert(
    pos: NavState,
    course_deg: float,
    grid: GridCfg = GridCfg(),
    warn_distance_cells: float = 1.0
) -> Optional[str]:
    """
    If you're within 1 cell (default) of any boundary *in the direction of travel*,
    return a short alert string like 'Approaching west boundary (A/col 0)'.
    Otherwise None.
    """
    # Direction unit vector in grid cells
    rad = math.radians(course_deg % 360.0)
    ux = math.sin(rad)
    uy = math.cos(rad)

    # Projected distances to each boundary along heading
    alerts = []

    if ux < -1e-6:  # moving west
        dist_cells = (pos.x - 0.0)
        if dist_cells <= warn_distance_cells:
            alerts.append("west (column A)")
    elif ux > 1e-6:  # moving east
        dist_cells = ((grid.cols - 1e-6) - pos.x)
        if dist_cells <= warn_distance_cells:
            alerts.append("east (column Z)")

    if uy < -1e-6:  # moving south
        dist_cells = (pos.y - 0.0)
        if dist_cells <= warn_distance_cells:
            alerts.append("south (row 1)")
    elif uy > 1e-6:  # moving north
        dist_cells = ((grid.rows - 1e-6) - pos.y)
        if dist_cells <= warn_distance_cells:
            alerts.append("north (row 26)")

    if alerts:
        return "Approaching grid boundary: " + " & ".join(alerts)
    return None

# ---------- Convenience

def from_cell_center(cell: str) -> NavState:
    """Initialize continuous position at the center of a given cell."""
    x, y = parse_cell(cell)
    return NavState(float(x), float(y))

def describe(pos: NavState) -> str:
    x_i, y_i = snapped_cell(pos)
    return f"{format_cell(x_i, y_i)} @ ({pos.x:.2f},{pos.y:.2f})"

# ---------- Self-test / demo

def _demo():
    """
    Demo: Start at K13, course 090 at 15 kts for 5 ticks of 60 seconds.
    Expect roughly 0.25 nm per tick (15 kts = 0.25 nm/min), so quarter of a cell each minute eastward.
    """
    grid = GridCfg()
    pos = from_cell_center("K13")
    course = 90.0
    speed = 15.0
    dt = 60.0  # 1 minute per tick

    print("NAV DEMO — start", describe(pos))
    for i in range(5):
        alert = border_alert(pos, course, grid)
        if alert:
            print(f"[tick {i}] ALERT: {alert}")
        pos = step_position(pos, course, speed, dt, grid)
        print(f"[t+{int((i+1)*dt)}s] {describe(pos)}")

if __name__ == "__main__":
    _demo()