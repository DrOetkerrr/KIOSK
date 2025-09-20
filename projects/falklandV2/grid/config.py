"""Grid configuration for canonical coordinate system.

Defaults are a 40×40 master board with AA00… style labels using two letters
for columns and zero-padded decimal rows.
"""
from __future__ import annotations

from typing import Optional

# Defaults — can be overridden by callers before import of coords
MASTER_COLS: int = 40
MASTER_ROWS: int = 40
ROW_WIDTH_OVERRIDE: Optional[int] = None


def row_width() -> int:
    """Compute row label zero-padding width.

    If ROW_WIDTH_OVERRIDE is set, return that; otherwise derive from MASTER_ROWS.
    Example (40 rows): width=len(str(39)) == 2 → rows AA00..AA39
    """
    if ROW_WIDTH_OVERRIDE is not None:
        return int(ROW_WIDTH_OVERRIDE)
    try:
        return max(1, len(str(int(MASTER_ROWS) - 1)))
    except Exception:
        return 2


ROW_WIDTH: int = row_width()

