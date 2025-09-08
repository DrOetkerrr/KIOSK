#!/usr/bin/env python3
"""
Falklands V2 — Engagement engine (readiness + shot resolution)
Pure helpers (no file I/O). Web layer handles arming + persistence.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List
import random

WeaponKey = str

# ---------- datatypes

@dataclass(frozen=True)
class CheckResult:
    key: WeaponKey
    name: str
    ready: Optional[bool]   # True/False/None (None = no lock / undefined)
    reason: str
    ammo_text: str
    range_text: str

@dataclass(frozen=True)
class FireRequest:
    weapon: WeaponKey
    target_range_nm: float
    target_type: str  # "air" or "ship"

@dataclass(frozen=True)
class FireOutcome:
    ok: bool
    weapon: WeaponKey
    target_type: str
    range_nm: float
    in_range: bool
    pk_used: float
    hit: Optional[bool]
    reason: str
    ammo_before: str
    ammo_after: str

# ---------- helpers

def _fmt_range(rdef: Any) -> str:
    if rdef is None:
        return "—"
    if isinstance(rdef, (int, float)):
        return f"≤{float(rdef):.1f} nm"
    if isinstance(rdef, list) and len(rdef) == 2:
        lo, hi = rdef
        parts = []
        if lo is not None: parts.append(f"≥{float(lo):.1f}")
        if hi is not None: parts.append(f"≤{float(hi):.1f}")
        return ("–".join(parts) + " nm") if parts else "—"
    return "—"

def _in_range(rdef: Any, rng_nm: float) -> Optional[bool]:
    if rdef is None:
        return None
    try:
        if isinstance(rdef, (int, float)):
            return rng_nm <= float(rdef)
        if isinstance(rdef, list) and len(rdef) == 2:
            lo = float(rdef[0]) if rdef[0] is not None else None
            hi = float(rdef[1]) if rdef[1] is not None else None
            if lo is not None and rng_nm < lo: return False
            if hi is not None and rng_nm > hi: return False
            return True
    except Exception:
        return None
    return None

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t

def _interp_pk(x: float, pts: List[Tuple[float, float]]) -> float:
    if not pts:
        return 0.0
    pts = sorted(pts, key=lambda p: p[0])
    if x <= pts[0][0]: return max(0.0, min(1.0, pts[0][1]))
    if x >= pts[-1][0]: return max(0.0, min(1.0, pts[-1][1]))
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        if x0 <= x <= x1:
            t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
            y = _lerp(y0, y1, t)
            return max(0.0, min(1.0, y))
    return 0.0

# ---------- default Pk curves (used if ship.json lacks pk)

DEFAULT_PK: Dict[str, Dict[str, List[Tuple[float, float]]]] = {
    "exocet_mm38": {"ship": [(5,0.35),(10,0.55),(15,0.52),(20,0.48),(22,0.45)], "air":[]},
    "gun_4_5in":  {"ship": [(2,0.12),(6,0.25),(12,0.18)], "air":[]},
    "seacat":     {"air":  [(0.5,0.10),(1.5,0.18),(2.5,0.16),(3.0,0.12),(3.5,0.05)], "ship":[]},
    "oerlikon_20mm":{"air":[(0.2,0.25),(0.5,0.35),(0.8,0.28),(1.0,0.15)], "ship":[]},
    "gam_bo1_20mm":{"air":[(0.2,0.28),(0.5,0.38),(0.8,0.30),(1.0,0.18)], "ship":[]},
    "corvus_chaff":{"air":[], "ship":[]}
}

def _range_def(wkey: WeaponKey, wdef: Dict[str, Any]) -> Any:
    if wkey == "gun_4_5in":
        return wdef.get("effective_max_nm", wdef.get("range_nm"))
    return wdef.get("range_nm")

def _ammo_strings_and_ok(wkey: WeaponKey, wdef: Dict[str, Any]) -> tuple[str, str, bool]:
    if wkey == "gun_4_5in":
        he = int(wdef.get("ammo_he", 0)); illum = int(wdef.get("ammo_illum", 0))
        return f"HE={he} ILLUM={illum}", f"HE={max(0, he-1)} ILLUM={illum}", he > 0
    elif wkey in ("seacat","oerlikon_20mm","gam_bo1_20mm","exocet_mm38"):
        r = int(wdef.get("rounds", 0))
        return f"{r}", f"{max(0, r-1)}", r > 0
    elif wkey == "corvus_chaff":
        s = int(wdef.get("salvoes", 0))
        return f"{s}", f"{max(0, s-1)}", s > 0
    else:
        return "?", "?", False

def _pk_points_for(wkey: WeaponKey, wdef: Dict[str, Any], target_type: str) -> List[Tuple[float, float]]:
    pk = (wdef.get("pk") or {}).get(target_type)
    if isinstance(pk, list) and pk and isinstance(pk[0], (list, tuple)) and len(pk[0]) == 2:
        pts: List[Tuple[float, float]] = []
        for it in pk:
            try: pts.append((float(it[0]), float(it[1])))
            except Exception: continue
        if pts: return pts
    return DEFAULT_PK.get(wkey, {}).get(target_type, [])

def weapon_valid_for_target(wkey: WeaponKey, wdef: Dict[str, Any], target_type: Optional[str]) -> Optional[bool]:
    """
    True if this weapon has a Pk definition for the given target_type.
    False if it clearly does not (e.g., Exocet vs air).
    None if target_type is unknown (e.g., no lock).
    """
    if target_type is None:
        return None
    pts = _pk_points_for(wkey, wdef, target_type)
    # Offensive weapons must have non-empty pk for that target type.
    if wkey == "corvus_chaff":
        return None
    return bool(pts)

# ---------- readiness

def check_weapon(key: WeaponKey, wdef: Dict[str, Any], target_range_nm: Optional[float]) -> CheckResult:
    name = wdef.get("name") or key
    if key == "gun_4_5in":
        ammo_he = int(wdef.get("ammo_he", 0)); ammo_il = int(wdef.get("ammo_illum", 0))
        ammo_ok = ammo_he > 0
        ammo_text = f"HE={ammo_he} ILLUM={ammo_il}"
        rng_def = _range_def(key, wdef); rng_txt = _fmt_range(rng_def)
    elif key in ("seacat","oerlikon_20mm","gam_bo1_20mm","exocet_mm38"):
        rounds = int(wdef.get("rounds", 0)); ammo_ok = rounds > 0
        ammo_text = f"{rounds}"
        rng_def = _range_def(key, wdef); rng_txt = _fmt_range(rng_def)
    elif key == "corvus_chaff":
        salvoes = int(wdef.get("salvoes", 0)); ammo_ok = salvoes > 0
        ammo_text = f"{salvoes}"; rng_def = None; rng_txt = "—"
    else:
        ammo_ok = False; ammo_text = "?"; rng_def = None; rng_txt = "—"

    if key == "corvus_chaff":
        if not ammo_ok: return CheckResult(key, name, False, "no ammo", ammo_text, rng_txt)
        return CheckResult(key, name, None, "not range-gated", ammo_text, rng_txt)

    if target_range_nm is None:
        return CheckResult(key, name, None, "no locked target", ammo_text, rng_txt)

    in_rng = _in_range(rdef := rng_def, target_range_nm)
    if not ammo_ok:       return CheckResult(key, name, False, "no ammo", ammo_text, rng_txt)
    if in_rng is False:   return CheckResult(key, name, False, "out of range", ammo_text, rng_txt)
    if in_rng is None:    return CheckResult(key, name, None, "range undefined", ammo_text, rng_txt)
    return CheckResult(key, name, True, "ready", ammo_text, rng_txt)

def assess_all(ship_cfg: Dict[str, Any], target_range_nm: Optional[float]) -> List[CheckResult]:
    w = ship_cfg.get("weapons", {})
    order = ["gun_4_5in","seacat","oerlikon_20mm","gam_bo1_20mm","exocet_mm38","corvus_chaff"]
    out: List[CheckResult] = []
    for key in order:
        if key in w: out.append(check_weapon(key, w[key], target_range_nm))
    for key, wdef in w.items():
        if key not in order: out.append(check_weapon(key, wdef, target_range_nm))
    return out

# ---------- fire once

def fire_once(ship_cfg: Dict[str, Any], req: FireRequest, *, rng=random) -> FireOutcome:
    wdefs = ship_cfg.get("weapons", {})
    wdef = wdefs.get(req.weapon)
    if not wdef:
        return FireOutcome(False, req.weapon, req.target_type, req.target_range_nm, False, 0.0, None, "unknown weapon", "?", "?")

    ammo_before, ammo_after, ammo_ok = _ammo_strings_and_ok(req.weapon, wdef)
    rdef = _range_def(req.weapon, wdef)
    in_rng = _in_range(rdef, req.target_range_nm)

    if not ammo_ok:
        return FireOutcome(False, req.weapon, req.target_type, req.target_range_nm, bool(in_rng), 0.0, None, "no ammo", ammo_before, ammo_before)
    if req.weapon == "corvus_chaff":
        return FireOutcome(False, req.weapon, req.target_type, req.target_range_nm, True, 0.0, None, "defensive countermeasure", ammo_before, ammo_before)
    if in_rng is False:
        return FireOutcome(False, req.weapon, req.target_type, req.target_range_nm, False, 0.0, None, "out of range", ammo_before, ammo_before)
    if in_rng is None:
        return FireOutcome(False, req.weapon, req.target_type, req.target_range_nm, False, 0.0, None, "range undefined", ammo_before, ammo_before)

    ttype = req.target_type.lower().strip()
    if ttype not in ("air","ship"):
        return FireOutcome(False, req.weapon, req.target_type, req.target_range_nm, True, 0.0, None, "invalid target type", ammo_before, ammo_before)

    pts = _pk_points_for(req.weapon, wdef, ttype)
    if not pts:
        return FireOutcome(False, req.weapon, req.target_type, req.target_range_nm, True, 0.0, None, "weapon not suitable for target", ammo_before, ammo_before)

    pk = _interp_pk(req.target_range_nm, pts)
    hit = (rng.random() < pk)

    return FireOutcome(True, req.weapon, ttype, req.target_range_nm, True, round(pk,3), hit, "fired", ammo_before, ammo_after)

# ---------- UI convenience

def in_range_flag(ship_cfg: Dict[str, Any], weapon: str, locked_range_nm: Optional[float], target_type: Optional[str]) -> Optional[bool]:
    """
    Returns:
      True  -> weapon valid vs target type AND range gate passes
      False -> weapon valid vs target type but out of range
      None  -> no lock OR weapon not valid vs that target type
    """
    wdef = (ship_cfg.get("weapons") or {}).get(weapon)
    if not wdef:
        return None
    if weapon == "corvus_chaff":
        return None
    if target_type is None or locked_range_nm is None:
        return None
    if not weapon_valid_for_target(weapon, wdef, target_type):
        return None
    return _in_range(_range_def(weapon, wdef), float(locked_range_nm))