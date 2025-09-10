from __future__ import annotations
from typing import Tuple, Dict, Any
from .radar import Contact, nm_distance

# Try to import WORLD_N; fall back to 40 if unavailable
try:
    from .radar import WORLD_N  # type: ignore
except Exception:
    try:
        from projects.falklandV2.radar import WORLD_N  # type: ignore
    except Exception:
        WORLD_N = 40  # type: ignore

# Board size for A..Z and 1..26 grid
BOARD_N = 26

def world_to_cell(x: float, y: float, world_n: float = WORLD_N, board_n: int = BOARD_N) -> str:
    def idx(v: float) -> int:
        if v <= 0:
            return 0
        if v >= world_n:
            return board_n - 1
        # proportional index with rounding
        return int(round((v / world_n) * (board_n - 1)))
    col_i = idx(x)                # 0..25
    row_i = idx(y)                # 0..25
    col_letter = chr(ord('A') + col_i)
    return f"{col_letter}{row_i + 1}"

def get_own_xy(state: Dict[str, Any]) -> Tuple[float, float]:
    ship = state.get("ship", {}) if isinstance(state, dict) else {}
    x = float(ship.get("col", 50))
    y = float(ship.get("row", 50))
    return x, y

def contact_to_ui(c: Contact, own_xy: Tuple[float, float]) -> Dict[str, Any]:
    ox, oy = own_xy
    rng = round(nm_distance(c.x, c.y, ox, oy), 2)
    crs = int(round(c.course_deg)) % 360
    spd = int(round(c.speed_kts * 0.75))
    cid = int(c.id)
    cell = world_to_cell(c.x, c.y)
    typ = str(c.allegiance)
    name = str(c.name)
    # Flat primitives with exact keys the UI reads; include label-style aliases for compatibility
    return {
        "id": cid,
        "ID": cid,
        "cell": cell,
        "name": name,
        "type": typ,
        "range_nm": rng,
        "Range": rng,
        "course": crs,
        "CRS": crs,
        "speed": spd,
        "SPD": spd,
    }
