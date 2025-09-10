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

# Board size for A..Z and 1..26 grid, centered inside 40x40 world
BOARD_N = 26
BOARD_MIN = (WORLD_N - BOARD_N) / 2.0  # 7.0 for 40→26 center window

def world_to_cell(x: float, y: float, world_n: float = WORLD_N, board_n: int = BOARD_N) -> str:
    """Map world (x,y) in 0..WORLD_N into captain grid A..Z,1..26 centered in world.
    Clamps outside positions to board edges. K13 should correspond to x=BOARD_MIN+10, y=BOARD_MIN+12.
    """
    bx = x - BOARD_MIN
    by = y - BOARD_MIN
    col_i = max(0, min(board_n - 1, int(round(bx))))
    row_i = max(0, min(board_n - 1, int(round(by))))
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
