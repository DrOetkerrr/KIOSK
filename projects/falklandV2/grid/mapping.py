from __future__ import annotations

"""Mapping helpers between continuous world coordinates and AA00 grid labels.

World coordinates are floats in 0..WORLD_N (typically 40). Grid indices are
0-based for both columns and rows. Formatting of labels delegates to coords.
"""

from typing import Tuple

from .config import MASTER_COLS, MASTER_ROWS
from .coords import format_coord, parse_coord, in_bounds


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def world_to_index(x: float, y: float, *, world_n: float = 40.0,
                   cols: int = MASTER_COLS, rows: int = MASTER_ROWS) -> Tuple[int, int]:
    """Map world floats (0..world_n) to nearest grid indices.

    Rounds to nearest cell. Origin is top-left.
    """
    if world_n <= 0:
        world_n = 40.0
    xf = _clamp(float(x), 0.0, float(world_n))
    yf = _clamp(float(y), 0.0, float(world_n))
    ci = int(round((xf / world_n) * max(0, int(cols) - 1)))
    ri = int(round((yf / world_n) * max(0, int(rows) - 1)))
    # Ensure within bounds
    ci = max(0, min(int(cols) - 1, ci))
    ri = max(0, min(int(rows) - 1, ri))
    return ci, ri


def index_to_world(col_index: int, row_index: int, *, world_n: float = 40.0,
                   cols: int = MASTER_COLS, rows: int = MASTER_ROWS) -> Tuple[float, float]:
    """Map grid indices back to world floats (0..world_n) at cell centers.
    """
    if not in_bounds(col_index, row_index, cols=cols, rows=rows):
        raise ValueError("indices out of bounds")
    if cols <= 1 or rows <= 1:
        return (0.0, 0.0)
    x = (float(col_index) / float(cols - 1)) * float(world_n)
    y = (float(row_index) / float(rows - 1)) * float(world_n)
    return x, y


def world_to_label(x: float, y: float, *, world_n: float = 40.0,
                   cols: int = MASTER_COLS, rows: int = MASTER_ROWS) -> str:
    ci, ri = world_to_index(x, y, world_n=world_n, cols=cols, rows=rows)
    return format_coord(ci, ri)


def label_to_world(label: str, *, world_n: float = 40.0,
                   cols: int = MASTER_COLS, rows: int = MASTER_ROWS) -> Tuple[float, float]:
    ci, ri = parse_coord(label)
    return index_to_world(ci, ri, world_n=world_n, cols=cols, rows=rows)

