from __future__ import annotations
from typing import Dict
from ..core.state import FalklandsState

COLS = [chr(ord('A') + i) for i in range(26)]
ROWS = list(range(1, 27))

def _valid_col(c: str) -> bool:
    return c.upper() in COLS

def _valid_row(r: int) -> bool:
    return 1 <= r <= 26

def _clamp_row(r: int) -> int:
    return min(26, max(1, r))

def _clamp_col_idx(i: int) -> int:
    return min(25, max(0, i))

class MapGridSystem:
    """
    26x26 grid sectors: columns A..Z, rows 1..26.
      /map place col=H row=12
      /map where
      /map show span=5   (renders a small window around the ship)
    """
    def __init__(self, st: FalklandsState):
        self.st = st
        m = self.st.data.setdefault("map", {})
        m.setdefault("col", "M")
        m.setdefault("row", 13)

    def place(self, args: Dict[str, str]) -> str:
        col = args.get("col", "").upper()
        try:
            row = int(args.get("row", "0"))
        except ValueError:
            return "MAP: invalid row (must be 1..26)"

        if not _valid_col(col):
            return "MAP: invalid column (use A..Z)"
        if not _valid_row(row):
            return "MAP: invalid row (use 1..26)"

        self.st.data["map"]["col"] = col
        self.st.data["map"]["row"] = row
        return f"MAP: ship placed at {col}-{row:02d}"

    def where(self, args: Dict[str, str]) -> str:
        m = self.st.data["map"]
        return f"MAP: current sector {m['col']}-{m['row']:02d}"

    def show(self, args: Dict[str, str]) -> str:
        """
        Render a local window around the ship.
        span=5 -> 11x11 window (5 each side + center)
        """
        try:
            span = int(args.get("span", "5"))
        except ValueError:
            span = 5
        span = max(1, min(12, span))

        m = self.st.data["map"]
        col = m["col"]; row = m["row"]

        # Center indices
        c_idx = COLS.index(col)
        r_idx = row - 1

        # Window bounds
        ci0 = _clamp_col_idx(c_idx - span)
        ci1 = _clamp_col_idx(c_idx + span)
        r0 = _clamp_row(r_idx + 1 - span) - 1
        r1 = _clamp_row(r_idx + 1 + span) - 1

        cols_header = "   " + " ".join(COLS[ci0:ci1+1])
        lines = [cols_header]

        for ri in range(r0, r1 + 1):
            row_num = f"{ri+1:02d}"
            row_cells = []
            for ci in range(ci0, ci1 + 1):
                mark = "·"
                if ci == c_idx and ri == r_idx:
                    mark = "S"  # Ship
                row_cells.append(mark)
            lines.append(f"{row_num} " + " ".join(row_cells))

        return "MAP window (S=ship):\n" + "\n".join(lines)