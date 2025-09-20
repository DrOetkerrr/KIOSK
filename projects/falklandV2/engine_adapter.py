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
LEGACY_SPAN = 100.0

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

def _cell_to_world(cell: str | None) -> Tuple[float, float] | None:
    if not cell:
        return None
    s = str(cell).strip().upper()
    if not s:
        return None
    i = 0
    while i < len(s) and s[i].isalpha():
        i += 1
    letters = s[:i] or "A"
    digits = s[i:] or "1"
    col_idx = 0
    for ch in letters:
        if 'A' <= ch <= 'Z':
            col_idx = col_idx * 26 + (ord(ch) - ord('A') + 1)
    try:
        row_idx = int(digits)
    except Exception:
        row_idx = 1
    col_idx = max(1, min(BOARD_N, col_idx)) - 1
    row_idx = max(1, min(BOARD_N, row_idx)) - 1
    return BOARD_MIN + float(col_idx), BOARD_MIN + float(row_idx)


def _scale_legacy(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value <= float(WORLD_N):
        return value
    return (value / LEGACY_SPAN) * float(WORLD_N)


def get_own_xy(state: Dict[str, Any]) -> Tuple[float, float]:
    ship = state.get("ship", {}) if isinstance(state, dict) else {}
    cell_world = _cell_to_world(ship.get("cell"))

    def _from_pos(pos: Dict[str, Any]) -> Tuple[float, float] | None:
        if not isinstance(pos, dict):
            return None
        try:
            px = float(pos.get("x"))
            py = float(pos.get("y"))
        except Exception:
            return None
        if cell_world is not None:
            idx_x = cell_world[0] - BOARD_MIN
            idx_y = cell_world[1] - BOARD_MIN
            if abs(px - idx_x) <= 0.75 and abs(py - idx_y) <= 0.75:
                return (cell_world[0] + (px - idx_x), cell_world[1] + (py - idx_y))
        if 0.0 <= px <= float(WORLD_N) and 0.0 <= py <= float(WORLD_N):
            return (px, py)
        return (BOARD_MIN + px, BOARD_MIN + py)

    def _from_ship_position(pos: Dict[str, Any]) -> Tuple[float, float] | None:
        if not isinstance(pos, dict):
            return None
        try:
            px = float(pos.get("col_f"))
            py = float(pos.get("row_f"))
        except Exception:
            return None
        return (_scale_legacy(px), _scale_legacy(py))

    def _from_ship_coords() -> Tuple[float, float] | None:
        try:
            px = float(ship.get("col"))
            py = float(ship.get("row"))
        except Exception:
            return None
        if cell_world is not None:
            idx_x = cell_world[0] - BOARD_MIN
            idx_y = cell_world[1] - BOARD_MIN
            if abs(px - idx_x) <= 0.75 and abs(py - idx_y) <= 0.75:
                return (cell_world[0] + (px - idx_x), cell_world[1] + (py - idx_y))
        return (_scale_legacy(px), _scale_legacy(py))

    for candidate in (
        _from_ship_position(state.get("ship_position")),
        _from_pos(ship.get("pos")),
        _from_ship_coords(),
        cell_world,
    ):
        if isinstance(candidate, tuple):
            x, y = candidate
            if 0.0 <= x <= float(WORLD_N) and 0.0 <= y <= float(WORLD_N):
                return x, y
    # Fallback: origin
    return 0.0, 0.0

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
    ui = {
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
    # Disambiguate multiple CAP flights by adding a pennant
    try:
        meta = getattr(c, 'meta', {}) or {}
        if bool(meta.get('cap_flight')):
            mid = meta.get('mission_id')
            if mid is not None:
                ui['pennant'] = f"CAP-{int(mid)}"
    except Exception:
        pass
    return ui
