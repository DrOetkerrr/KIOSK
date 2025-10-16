from __future__ import annotations
from typing import Tuple, Dict, Any
from .radar import Contact, nm_distance

# Try to import WORLD_N; fall back to 40 if unavailable
try:
    from .radar import WORLD_N  # type: ignore
except Exception:
    WORLD_N = 40  # type: ignore

from projects.falklandV2.grid.mapping import world_to_label, label_to_world

LEGACY_SPAN = 100.0


def world_to_cell(x: float, y: float) -> str:
    return world_to_label(float(x), float(y), world_n=float(WORLD_N))


def _scale_legacy(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value <= float(WORLD_N):
        return value
    return (value / LEGACY_SPAN) * float(WORLD_N)


def get_own_xy(state: Dict[str, Any]) -> Tuple[float, float]:
    ship = state.get("ship", {}) if isinstance(state, dict) else {}
    # Prefer explicit numeric world coords
    pos = ship.get("pos") if isinstance(ship, dict) else None
    if isinstance(pos, dict):
        try:
            px = float(pos.get("x")); py = float(pos.get("y"))
            if 0.0 <= px <= float(WORLD_N) and 0.0 <= py <= float(WORLD_N):
                return (px, py)
        except Exception:
            pass
    # Legacy fields (col,row) may be 0..WORLD_N or 0..100
    try:
        px = float(ship.get("col")); py = float(ship.get("row"))
        return (_scale_legacy(px), _scale_legacy(py))
    except Exception:
        return (0.0, 0.0)


def cell_to_world(cell: str) -> Tuple[float, float]:
    try:
        return label_to_world(str(cell or ""), world_n=float(WORLD_N))
    except Exception:
        return (0.0, 0.0)


def ship_cell_from_state(state: Dict[str, Any]) -> str:
    x, y = get_own_xy(state)
    return world_to_cell(x, y)


def radar_xy_from_state(state: Dict[str, Any]) -> Tuple[float, float]:
    x, y = get_own_xy(state)
    if x > float(WORLD_N) or y > float(WORLD_N):
        return (_scale_legacy(x), _scale_legacy(y))
    return (x, y)


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
    meta = getattr(c, 'meta', {}) or {}
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
        is_cap = bool(meta.get('cap_flight'))
        if is_cap:
            mid = meta.get('mission_id')
            if mid is not None:
                ui['pennant'] = f"CAP-{int(mid)}"
                ui['cap_mission_id'] = int(mid)
        ui['cap_flight'] = is_cap
        callsign = meta.get('callsign')
        if callsign:
            ui['cap_callsign'] = str(callsign)
        display_name = meta.get('display_name')
        if display_name and not name:
            ui['name'] = str(display_name)
        if meta.get('resupply') or str(meta.get('hull') or '').strip().lower() == 'resupply':
            ui['hull'] = 'resupply'
        if 'resupply_stage' in meta:
            ui.setdefault('meta', {})['resupply_stage'] = meta['resupply_stage']
    except Exception:
        pass
    return ui
