"""Canonical AA00 coordinate system helpers.

Spec summary:
- Columns are two uppercase letters AA..ZZ with base-26 (A=0..Z=25).
- Rows are zero-based decimal integers zero-padded to ROW_WIDTH digits.
- Origin is top-left at col_index=0, row_index=0.
"""
from __future__ import annotations

import re
from typing import Iterable, Iterator, List, Optional, Tuple

from .config import MASTER_COLS, MASTER_ROWS, ROW_WIDTH


def col_to_index(col2: str) -> int:
    """Convert two-letter column label to zero-based index.

    Example: 'AA'->0, 'AB'->1, ..., 'AZ'->25, 'BA'->26.
    """
    if not isinstance(col2, str) or len(col2) != 2 or not col2.isupper() or not col2.isalpha():
        raise ValueError(f"Bad column label: {col2!r}")
    a, b = col2[0], col2[1]
    return (ord(a) - ord('A')) * 26 + (ord(b) - ord('A'))


def index_to_col(i: int) -> str:
    """Convert zero-based index to two-letter column label.

    Example: 27 == 'BB'.
    """
    try:
        ii = int(i)
    except Exception:
        raise ValueError(f"Bad column index: {i!r}")
    if ii < 0:
        raise ValueError(f"Negative column index: {i}")
    hi = ii // 26
    lo = ii % 26
    return chr(ord('A') + hi) + chr(ord('A') + lo)


def format_coord(col_index: int, row_index: int, *, row_width: int = ROW_WIDTH) -> str:
    """Format indices into 'AA00' style string.

    Raises ValueError if indices are negative or row width invalid.
    """
    if col_index < 0 or row_index < 0:
        raise ValueError("Indices must be non-negative")
    if row_width <= 0:
        raise ValueError("row_width must be positive")
    return f"{index_to_col(int(col_index))}{int(row_index):0{row_width}d}"


def _pattern(row_width: int = ROW_WIDTH) -> re.Pattern[str]:
    return re.compile(rf"^([A-Z]{{2}})(\d{{{int(row_width)},}})$")


def parse_coord(s: str, *, row_width: int = ROW_WIDTH) -> Tuple[int, int]:
    """Parse 'AA00' to (col_index,row_index).

    Strict: requires two uppercase letters and exactly ROW_WIDTH or more digits.
    """
    if not isinstance(s, str):
        raise ValueError("label must be a string")
    m = _pattern(row_width).match(s)
    if not m:
        raise ValueError(f"Bad coordinate: {s!r}")
    col = m.group(1)
    row_str = m.group(2)
    ci = col_to_index(col)
    ri = int(row_str)
    return (ci, ri)


def in_bounds(col_index: int, row_index: int, *, cols: int = MASTER_COLS, rows: int = MASTER_ROWS) -> bool:
    return 0 <= col_index < int(cols) and 0 <= row_index < int(rows)


def center_subboard(master_cols: int, master_rows: int, sub_cols: int, sub_rows: int) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """Return ((tl_col,tl_row),(br_col,br_row)) inclusive bounds for a centered sub-board.
    Validates that sub fits entirely in master.
    """
    mc, mr, sc, sr = int(master_cols), int(master_rows), int(sub_cols), int(sub_rows)
    if sc > mc or sr > mr:
        raise ValueError("sub-board larger than master")
    tl_c = (mc - sc) // 2
    tl_r = (mr - sr) // 2
    br_c = tl_c + sc - 1
    br_r = tl_r + sr - 1
    if not (0 <= tl_c <= br_c < mc and 0 <= tl_r <= br_r < mr):
        raise ValueError("centered sub-board out of bounds")
    return (tl_c, tl_r), (br_c, br_r)


def center_subboard_labels(master_cols: int = MASTER_COLS, master_rows: int = MASTER_ROWS, sub_cols: int = 30, sub_rows: int = 30) -> Tuple[str, str]:
    (tl_c, tl_r), (br_c, br_r) = center_subboard(master_cols, master_rows, sub_cols, sub_rows)
    return format_coord(tl_c, tl_r), format_coord(br_c, br_r)


def neighbors4(col_index: int, row_index: int, *, cols: int = MASTER_COLS, rows: int = MASTER_ROWS) -> List[Tuple[int, int]]:
    """4-neighborhood within bounds (N,S,E,W)."""
    out: List[Tuple[int, int]] = []
    for dc, dr in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        c, r = col_index + dc, row_index + dr
        if in_bounds(c, r, cols=cols, rows=rows):
            out.append((c, r))
    return out


def neighbors8(col_index: int, row_index: int, *, cols: int = MASTER_COLS, rows: int = MASTER_ROWS) -> List[Tuple[int, int]]:
    """8-neighborhood within bounds (includes diagonals)."""
    out: List[Tuple[int, int]] = []
    for dc in (-1, 0, 1):
        for dr in (-1, 0, 1):
            if dc == 0 and dr == 0:
                continue
            c, r = col_index + dc, row_index + dr
            if in_bounds(c, r, cols=cols, rows=rows):
                out.append((c, r))
    return out


# Convenience aliases
def from_index(col_index: int, row_index: int) -> str:
    return format_coord(col_index, row_index)


def to_index(label: str) -> Tuple[int, int]:
    return parse_coord(label)

