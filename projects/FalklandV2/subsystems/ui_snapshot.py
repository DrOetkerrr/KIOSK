# subsystems/ui_snapshot.py
"""
UI snapshot helpers for Falklands V2.
- weapons_snapshot(data_path, locked_range_nm) -> weapons dict for UI
- build_snapshot(eng, cap, convoy, paused, data_path) -> full UI snapshot dict

These functions are pure (no globals) and expect the caller to hold any locks.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import json

# Local subsystems
from subsystems import radar as rdar
from subsystems import contacts as cons
from subsystems import nav as navi
from subsystems import weapons as weap


# ---------- small helpers

def _read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))

def _rng_text(rdef: Any) -> str:
    if isinstance(rdef, (int, float)):
        return f"≤{float(rdef):.1f} nm"
    if isinstance(rdef, list) and len(rdef) == 2:
        lo = f"≥{rdef[0]}" if rdef[0] else ""
        hi = f"≤{rdef[1]}" if rdef[1] else ""
        dash = "–" if lo and hi else ""
        return f"{lo}{dash}{hi} nm" if (lo or hi) else "—"
    return "—"

def _in_range(rdef: Any, rng_nm: Optional[float]) -> Optional[bool]:
    if rng_nm is None or rdef is None:
        return None
    if isinstance(rdef, (int, float)):
        return rng_nm <= float(rdef)
    if isinstance(rdef, list) and len(rdef) == 2:
        lo = float(rdef[0]) if rdef[0] is not None else None
        hi = float(rdef[1]) if rdef[1] is not None else None
        if lo is not None and rng_nm < lo: return False
        if hi is not None and rng_nm > hi: return False
        return True
    return None


# ---------- public: weapons block

def weapons_snapshot(data_path: Path, locked_range_nm: Optional[float]) -> Dict[str, Any]:
    """
    Build the weapons info for the UI (legacy status line + simplified table).
    Only one ammo column per weapon.
    """
    ship_path = data_path / "ship.json"
    ship_name = "Own Ship"
    status_line = "WEAPONS: (no ship.json found)"
    table: List[Dict[str, Any]] = []

    if not ship_path.exists():
        return {"ship_name": ship_name, "status_line": status_line, "table": table}

    try:
        ship = _read_json(ship_path)
        name = ship.get("name", ship_name)
        klass = ship.get("class", "")
        ship_name = f"{name} ({klass})" if klass else name
        status_line = weap.weapons_status(ship)

        w = ship.get("weapons", {})

        # 4.5"
        if "gun_4_5in" in w:
            g = w["gun_4_5in"]
            ammo = int(g.get("ammo_he", 0))
            rdef = g.get("effective_max_nm", g.get("range_nm"))
            ready = _in_range(rdef, locked_range_nm)
            table.append({"name": "4.5in Mk.8", "ammo": ammo, "range": _rng_text(rdef), "ready": (ready and ammo > 0)})

        # Sea Cat
        if "seacat" in w:
            sc = w["seacat"]; rounds = int(sc.get("rounds", 0)); rdef = sc.get("range_nm")
            ready = _in_range(rdef, locked_range_nm)
            table.append({"name": "Sea Cat", "ammo": rounds, "range": _rng_text(rdef), "ready": (ready and rounds > 0)})

        # 20mm
        if "oerlikon_20mm" in w:
            o = w["oerlikon_20mm"]; rounds = int(o.get("rounds", 0)); rdef = o.get("range_nm")
            ready = _in_range(rdef, locked_range_nm)
            table.append({"name": "20mm Oerlikon", "ammo": rounds, "range": _rng_text(rdef), "ready": (ready and rounds > 0)})

        if "gam_bo1_20mm" in w:
            g2 = w["gam_bo1_20mm"]; rounds = int(g2.get("rounds", 0)); rdef = g2.get("range_nm")
            ready = _in_range(rdef, locked_range_nm)
            table.append({"name": "GAM-BO1 20mm", "ammo": rounds, "range": _rng_text(rdef), "ready": (ready and rounds > 0)})

        # Exocet
        if "exocet_mm38" in w:
            ex = w["exocet_mm38"]; rounds = int(ex.get("rounds", 0)); rdef = ex.get("range_nm")
            ready = _in_range(rdef, locked_range_nm)
            table.append({"name": "Exocet MM38", "ammo": rounds, "range": _rng_text(rdef), "ready": (ready and rounds > 0)})

        # Chaff
        if "corvus_chaff" in w:
            ch = w["corvus_chaff"]; salvoes = int(ch.get("salvoes", 0))
            table.append({"name": "Corvus chaff", "ammo": salvoes, "range": "—", "ready": None})

        return {"ship_name": ship_name, "status_line": status_line, "table": table}
    except Exception as e:
        return {"ship_name": ship_name, "status_line": f"WEAPONS: (error {e})", "table": table}


# ---------- public: full snapshot

def build_snapshot(eng: Any,
                   cap: Optional[Any],
                   convoy: Optional[Any],
                   paused: bool,
                   data_path: Path) -> Dict[str, Any]:
    """
    Assemble the complete UI snapshot.
    Assumes caller holds any required engine locks.
    """
    sx, sy = eng._ship_xy()
    course, speed = eng._ship_course_speed()

    # Escorts
    escorts: List[Dict[str, Any]] = []
    escorts_hud = "ESCORTS: —"
    if convoy is not None:
        snaps = convoy.update(sx, sy, course, speed, eng.pool.grid)
        escorts = [{
            "id": s.id, "name": s.name, "klass": s.klass, "type": s.type,
            "allegiance": s.allegiance, "cell": s.cell,
            "course_deg": s.course_deg, "speed_kts": s.speed_kts
        } for s in snaps]
        escorts_hud = convoy.hud_fragment(snaps)

    # Contacts (nearest 10)
    locked_id = eng.state.get("radar", {}).get("locked_contact_id")
    nearest = sorted(
        eng.pool.contacts,
        key=lambda c: cons.dist_nm_xy(c.x, c.y, sx, sy, eng.pool.grid)
    )[:10]
    contacts = [{
        "id": c.id,
        "cell": cons.format_cell(int(round(c.x)), int(round(c.y))),
        "type": c.type, "name": c.name, "allegiance": c.allegiance,
        "range_nm": round(cons.dist_nm_xy(c.x, c.y, sx, sy, eng.pool.grid), 1),
        "course_deg": round(c.course_deg, 0),
        "speed_kts": round(c.speed_kts_game, 0)
    } for c in nearest]

    # Locked target
    locked_snap = None
    if locked_id is not None:
        tgt = next((c for c in eng.pool.contacts if c.id == locked_id), None)
        if tgt is not None:
            locked_snap = {
                "id": tgt.id,
                "cell": cons.format_cell(int(round(tgt.x)), int(round(tgt.y))),
                "type": tgt.type, "name": tgt.name, "allegiance": tgt.allegiance,
                "range_nm": round(cons.dist_nm_xy(tgt.x, tgt.y, sx, sy, eng.pool.grid), 1),
                "course_deg": round(tgt.course_deg, 0),
                "speed_kts": round(tgt.speed_kts_game, 0)
            }

    # Weapons
    weapons = weapons_snapshot(data_path, locked_snap["range_nm"] if locked_snap else None)

    # CAP
    cap_snap = (cap.snapshot() if cap is not None else {"readiness": {}, "missions": []})

    # HUD
    hud_text = f"{eng.hud()} | {escorts_hud}"

    return {
        "hud": hud_text,
        "ship": {
            "cell": navi.format_cell(*navi.snapped_cell(
                navi.NavState(eng.state["ship"]["pos"]["x"], eng.state["ship"]["pos"]["y"])
            )),
            "course_deg": round(course, 1),
            "speed_kts": round(speed, 1)
        },
        "radar": {
            "locked_contact_id": locked_id,
            "locked_range_nm": locked_snap["range_nm"] if locked_snap else None,
            "status_line": rdar.status_line(eng.pool, (sx, sy), locked_id=locked_id, max_list=3)
        },
        "contacts": contacts,
        "weapons": weapons,
        "cap": cap_snap,
        "escorts": escorts,
        "locked_target": locked_snap,
        "paused": paused,
    }