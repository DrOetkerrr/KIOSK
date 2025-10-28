"""Grid coordinate helpers matching the legacy AA00…BN39 board layout."""

from __future__ import annotations

from typing import Tuple

MASTER_COLS = 40
MASTER_ROWS = 40
ROW_WIDTH = 2
WORLD_SIZE_NM = 40.0


def _clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


def _index_to_col(index: int) -> str:
    if index < 0:
        raise ValueError("column index must be non-negative")
    hi = index // 26
    lo = index % 26
    return chr(ord("A") + hi) + chr(ord("A") + lo)


def world_to_index(x_nm: float, y_nm: float) -> Tuple[int, int]:
    """Convert world coordinates (0..40 nm) into grid indices."""
    xf = _clamp(float(x_nm), 0.0, WORLD_SIZE_NM)
    yf = _clamp(float(y_nm), 0.0, WORLD_SIZE_NM)
    col = int(round((xf / WORLD_SIZE_NM) * (MASTER_COLS - 1)))
    row = int(round((yf / WORLD_SIZE_NM) * (MASTER_ROWS - 1)))
    col = max(0, min(MASTER_COLS - 1, col))
    row = max(0, min(MASTER_ROWS - 1, row))
    return col, row


def world_to_label(x_nm: float, y_nm: float) -> str:
    col, row = world_to_index(x_nm, y_nm)
    return f"{_index_to_col(col)}{row:0{ROW_WIDTH}d}"


def indices_to_label(col_index: int, row_index: int) -> str:
    if not (0 <= col_index < MASTER_COLS and 0 <= row_index < MASTER_ROWS):
        raise ValueError("indices out of bounds")
    return f"{_index_to_col(col_index)}{row_index:0{ROW_WIDTH}d}"
