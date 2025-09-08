#!/usr/bin/env python3
"""
Falklands V2 — Engagement (target-type aware + fire_once back-compat)

- Readiness helpers (weapon_valid_for_target / weapon_in_range / assess_all ...)
- Back-compat shims used by webdash.py: in_range_flag, weapon_range_text
- FireRequest dataclass and fire_once() used by /api/fire

Notes:
- fire_once mutates the provided ship_cfg (decrements ammo on success).
- Mode "test" ignores lock/range/type but still checks ammo.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List
from pathlib import Path
import argparse, json, sys

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SHIP = DATA / "ship.json"

WeaponKey = str

# ---------- target typing ----------

def normalize_target_type(s: Optional[str]) -> str:
    if not s:
        return "unknown"
    t = s.strip().lower()
    if any(k in t for k in ("air", "helicopter", "helo", "jet", "mirage", "skyhawk", "dagger", "canberra")):
        return "air"
    if any(k in t for k in ("missile", "exocet", "asm")):
        return "missile"
    if any(k in t for k in ("ship", "tanker", "merchant", "frigate", "destroyer", "trawler", "landing craft", "stores ship", "fleet")):
        return "surface"
    return "unknown"

VALID_TARGETS: Dict[str, Tuple[str, ...]] = {
    # AA
    "seacat": ("air", "missile"),
    "oerlikon_20mm": ("air", "missile"),
    "gam_bo1_20mm": ("air", "missile"),
    # ASuW
    "gun_4_5in": ("surface",),
    "exocet_mm38": ("surface",),
    # Utility
    "corvus_chaff": ("missile", "air", "surface", "unknown"),
}

@dataclass(frozen=True)
class CheckResult:
    key: WeaponKey
    name: str
    ready: Optional[bool]
    in_range: Optional[bool]
    reason: str
    ammo_text: str
    range_text: str

# ---------- IO ----------

def _load_ship() -> Dict[str, Any]:
    try:
        return json.loads(SHIP.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("engage.py: data/ship.json not found.", file=sys.stderr)
        return {"weapons": {}}
    except Exception as e:
        print(f"engage.py: failed to read ship.json: {e}", file=sys.stderr)
        return {"weapons": {}}

# ---------- helpers ----------

def _fmt_range(rdef: Any) -> str:
    if rdef is None: return "—"
    if isinstance(rdef, (int, float)): return f"≤{float(rdef):.1f} nm"
    if isinstance(rdef, list) and len(rdef) == 2:
        lo, hi = rdef
        parts = []
        if lo is not None: parts.append(f"≥{float(lo):.1f}")
        if hi is not None: parts.append(f"≤{float(hi):.1f}")
        return ("–".join(parts) + " nm") if parts else "—"
    return "—"

def _in_range(rdef: Any, rng_nm: float) -> Optional[bool]:
    if rdef is None: return None
    try:
        if isinstance(rdef, (int, float)): return rng_nm <= float(rdef)
        if isinstance(rdef, list) and len(rdef) == 2:
            lo = float(rdef[0]) if rdef[0] is not None else None
            hi = float(rdef[1]) if rdef[1] is not None else None
            if lo is not None and rng_nm < lo: return False
            if hi is not None and rng_nm > hi: return False
            return True
    except Exception:
        return None
    return None

def _ammo_and_range(key: WeaponKey, wdef: Dict[str, Any]) -> Tuple[bool, str, Any, str]:
    if key == "gun_4_5in":
        ammo_he = int(wdef.get("ammo_he", 0))
        ammo_il = int(wdef.get("ammo_illum", 0))
        ammo_ok = ammo_he > 0
        ammo_text = f"HE={ammo_he} ILLUM={ammo_il}"
        rng_def = wdef.get("effective_max_nm", wdef.get("range_nm", 0.0))
        rng_txt = _fmt_range(rng_def)
    elif key in ("seacat", "oerlikon_20mm", "gam_bo1_20mm", "exocet_mm38"):
        rounds = int(wdef.get("rounds", 0))
        ammo_ok = rounds > 0
        ammo_text = f"{rounds}"
        rng_def = wdef.get("range_nm")
        rng_txt = _fmt_range(rng_def)
    elif key == "corvus_chaff":
        salvoes = int(wdef.get("salvoes", 0))
        ammo_ok = salvoes > 0
        ammo_text = f"{salvoes}"
        rng_def = None
        rng_txt = "—"
    else:
        ammo_ok = False
        ammo_text = "?"
        rng_def = None
        rng_txt = "—"
    return ammo_ok, ammo_text, rng_def, rng_txt

# ---------- public helpers (used by webdash) ----------

def weapon_valid_for_target(key: WeaponKey, wdef: Dict[str, Any], target_type: Optional[str]) -> Optional[bool]:
    if target_type is None: return None
    ttype = normalize_target_type(target_type)
    return ttype in VALID_TARGETS.get(key, ())

def weapon_in_range(wdef: Dict[str, Any], rng_nm: Optional[float]) -> Optional[bool]:
    if rng_nm is None: return None
    rdef = wdef.get("effective_max_nm", wdef.get("range_nm"))
    return _in_range(rdef, float(rng_nm))

def weapon_range_text(wdef: Dict[str, Any]) -> str:
    rdef = wdef.get("effective_max_nm", wdef.get("range_nm"))
    return _fmt_range(rdef)

def in_range_flag(ship_cfg: Dict[str, Any], key: WeaponKey, rng_nm: Optional[float], _target_type_unused: Optional[str]=None) -> Optional[bool]:
    if rng_nm is None: return None
    wdef = ship_cfg.get("weapons", {}).get(key)
    if not wdef: return None
    rdef = wdef.get("effective_max_nm", wdef.get("range_nm"))
    return _in_range(rdef, float(rng_nm))

# ---------- readiness table ----------

def check_weapon(key: WeaponKey, wdef: Dict[str, Any], target_range_nm: Optional[float], target_type: Optional[str]) -> CheckResult:
    name = wdef.get("name") or key
    ammo_ok, ammo_text, rng_def, rng_txt = _ammo_and_range(key, wdef)

    if key == "corvus_chaff":
        if not ammo_ok:
            return CheckResult(key, name, False, None, "no ammo", ammo_text, rng_txt)
        return CheckResult(key, name, None, None, "not range-gated", ammo_text, rng_txt)

    if target_range_nm is None or target_type is None:
        return CheckResult(key, name, None, None, "no locked target", ammo_text, rng_txt)

    ttype = normalize_target_type(target_type)
    valid = ttype in VALID_TARGETS.get(key, ())
    in_rng = _in_range(rng_def, float(target_range_nm)) if target_range_nm is not None else None

    if not valid:
        return CheckResult(key, name, False, in_rng, f"invalid vs {ttype}", ammo_text, rng_txt)
    if not ammo_ok:
        return CheckResult(key, name, False, in_rng, "no ammo", ammo_text, rng_txt)
    if in_rng is False:
        return CheckResult(key, name, False, in_rng, "out of range", ammo_text, rng_txt)
    if in_rng is None:
        return CheckResult(key, name, None, in_rng, "range undefined", ammo_text, rng_txt)
    return CheckResult(key, name, True, in_rng, "ready", ammo_text, rng_txt)

def assess_all(ship_cfg: Dict[str, Any], target_range_nm: Optional[float], target_type: Optional[str]) -> List[CheckResult]:
    w = ship_cfg.get("weapons", {})
    order = ["gun_4_5in", "seacat", "oerlikon_20mm", "gam_bo1_20mm", "exocet_mm38", "corvus_chaff"]
    results: List[CheckResult] = []
    for key in order:
        if key in w:
            results.append(check_weapon(key, w[key], target_range_nm, target_type))
    for key, wdef in w.items():
        if key not in order:
            results.append(check_weapon(key, wdef, target_range_nm, target_type))
    return results

# ---------- firing API (back-compat with webdash) ----------

@dataclass
class FireRequest:
    weapon: WeaponKey
    target_range_nm: Optional[float]
    target_type: Optional[str]
    mode: str = "fire"   # "fire" or "test"

def _dec_ammo_inplace(wdef: Dict[str, Any], key: WeaponKey) -> int:
    """Decrement ammo and return new amount. Conservative defaults (1 per shot)."""
    if key == "gun_4_5in":
        cur = int(wdef.get("ammo_he", 0))
        cur = max(0, cur - 1)
        wdef["ammo_he"] = cur
        return cur
    if key in ("seacat", "exocet_mm38"):
        cur = int(wdef.get("rounds", 0))
        cur = max(0, cur - 1)
        wdef["rounds"] = cur
        return cur
    if key in ("oerlikon_20mm", "gam_bo1_20mm"):
        cur = int(wdef.get("rounds", 0))
        # Use small burst decrement; tweak later if desired
        step = 50 if cur >= 50 else 1
        cur = max(0, cur - step)
        wdef["rounds"] = cur
        return cur
    if key == "corvus_chaff":
        cur = int(wdef.get("salvoes", 0))
        cur = max(0, cur - 1)
        wdef["salvoes"] = cur
        return cur
    return 0

def fire_once(ship_cfg: Dict[str, Any], req: FireRequest) -> Dict[str, Any]:
    """
    Perform basic eligibility checks and decrement ammo if permitted.
    Returns a dict: {ok: bool, message: str, weapon: key, ammo_after: int}
    - mode=="fire": require locked target + valid type + in-range + ammo>0
    - mode=="test": ignore lock/range/type but still require ammo>0
    """
    key = req.weapon
    w = ship_cfg.get("weapons", {})
    if key not in w:
        return {"ok": False, "weapon": key, "message": "unknown weapon"}

    wdef = w[key]
    ammo_ok, _ammo_text, rng_def, _rng_txt = _ammo_and_range(key, wdef)

    # Test mode: only ammo gate
    if req.mode == "test":
        if not ammo_ok:
            return {"ok": False, "weapon": key, "message": "no ammo"}
        new_amt = _dec_ammo_inplace(wdef, key)
        return {"ok": True, "weapon": key, "message": "TEST FIRE", "ammo_after": new_amt}

    # Normal fire
    if not ammo_ok:
        return {"ok": False, "weapon": key, "message": "no ammo"}

    if req.target_type is None or req.target_range_nm is None:
        return {"ok": False, "weapon": key, "message": "no locked target"}

    ttype = normalize_target_type(req.target_type)
    if ttype not in VALID_TARGETS.get(key, ()):
        return {"ok": False, "weapon": key, "message": f"invalid vs {ttype}"}

    in_rng = _in_range(rng_def, float(req.target_range_nm)) if rng_def is not None else None
    if in_rng is False or in_rng is None:
        return {"ok": False, "weapon": key, "message": "out of range"}

    new_amt = _dec_ammo_inplace(wdef, key)
    return {"ok": True, "weapon": key, "message": "FIRED", "ammo_after": new_amt}

# ---------- CLI for quick sanity ----------

def _print_table(results: List[CheckResult], rng: Optional[float], ttype: Optional[str]) -> None:
    head = f"Target range: {rng:.2f} nm, type={ttype}" if rng is not None else "Target range: (none locked)"
    print(head)
    print("WEAPON                READY   IN-RANGE  AMMO                 RANGE              REASON")
    print("-----------------------------------------------------------------------------------------")
    for r in results:
        rdy = "READY " if r.ready is True else ("OUT   " if r.ready is False else "N/A   ")
        ir  = "True  " if r.in_range is True else ("False " if r.in_range is False else "N/A  ")
        print(f"{r.name:<20}  {rdy:<6}  {ir:<7}  {r.ammo_text:<20}  {r.range_text:<16}  {r.reason}")

def main() -> None:
    ap = argparse.ArgumentParser(description="Engagement readiness checker")
    ap.add_argument("--range", type=float, default=None)
    ap.add_argument("--tt", type=str, default=None)
    ap.add_argument("--fire", type=str, default=None, help="weapon key to fire (test with --mode)")
    ap.add_argument("--mode", type=str, default="fire", choices=["fire","test"])
    args = ap.parse_args()

    ship = _load_ship()
    rng = None if args.range is None else float(args.range)
    ttype = args.tt

    if args.fire:
        out = fire_once(ship, FireRequest(weapon=args.fire, target_range_nm=rng, target_type=ttype, mode=args.mode))
        print(out)
        return

    results = assess_all(ship, rng, ttype)
    _print_table(results, rng, ttype)

if __name__ == "__main__":
    main()