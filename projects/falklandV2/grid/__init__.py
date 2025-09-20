"""Grid utilities package (canonical coordinate system).

Exports:
- config: MASTER_COLS, MASTER_ROWS, ROW_WIDTH
- coords: conversion and helpers
"""

from .config import MASTER_COLS, MASTER_ROWS, ROW_WIDTH  # noqa: F401
from .coords import (  # noqa: F401
    col_to_index,
    index_to_col,
    format_coord,
    parse_coord,
    in_bounds,
    center_subboard,
    center_subboard_labels,
    neighbors4,
    neighbors8,
    to_index,
    from_index,
)

