#!/usr/bin/env python3
"""
Falklands V2 — Engagement readiness (Step 14)

Responsibility:
- Inspect ship.json weapons and report if a weapon is READY vs a target at range R (nm).
- Pure function style; no state mutations (no ammo decrement yet).
- Meant to be called by commander/webdash later.

Usage (CLI):
  python3 projects/FalklandV2/subsystems/engage.py --range 5.0
  python3 projects/FalklandV2/subsystems/engage.py --range 0.4
  python3 projects/FalklandV2/subsystems/engage.py   # no range → shows ammo only

Output: table of weapons + READY/OUT + reason.
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

@dataclass(frozen=True)
class CheckResult:
    key: WeaponKey
    name: str
    ready: Optional[bool]   # True/False/None (None = N/A, e.g., no locked target range given)
    reason: str
    ammo_text: str
    range_text: str

def _load_ship() -> Dict[str, Any]:
    try:
        return json.loads(SHIP.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("engage.py: data/ship.json not found.", file=sys.stderr)
        return {"weapons": {}}
    except Exception as e:
        print(f"engage.py: failed to read ship.json: {e}", file=sys.stderr)
        return {"weapons": {}}

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
            if lo is not None and rng_nm < lo:
                return False
            if hi is not None and rng_nm > hi:
                return False
            return True
    except Exception:
        return None
    return None

def check_weapon(key: WeaponKey, wdef: Dict[str, Any], target_range_nm: Optional[float]) -> CheckResult:
    """
    Compute readiness for one weapon. Does not mutate state.
    """
    name = wdef.get("name") or key
    # Ammo fields & display
    if key == "gun_4_5in":
        ammo_he = int(wdef.get("ammo_he", 0))
        ammo_il = int(wdef.get("ammo_illum", 0))
        ammo_ok = ammo_he > 0  # use HE for readiness
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
        # Unknown weapon; be conservative
        ammo_ok = False
        ammo_text = "?"
        rng_def = None
        rng_txt = "—"

    # Range logic
    if key == "corvus_chaff":
        # Chaff isn't locked-target range-based here; mark N/A unless ammo=0
        if not ammo_ok:
            return CheckResult(key, name, False, "no ammo", ammo_text, rng_txt)
        return CheckResult(key, name, None, "not range-gated", ammo_text, rng_txt)

    if target_range_nm is None:
        # No locked target: readiness unknown, report ammo
        return CheckResult(key, name, None, "no locked target", ammo_text, rng_txt)

    in_rng = _in_range(rng_def, target_range_nm)
    if not ammo_ok:
        return CheckResult(key, name, False, "no ammo", ammo_text, rng_txt)
    if in_rng is False:
        return CheckResult(key, name, False, "out of range", ammo_text, rng_txt)
    if in_rng is None:
        return CheckResult(key, name, None, "range undefined", ammo_text, rng_txt)
    return CheckResult(key, name, True, "ready", ammo_text, rng_txt)

def assess_all(ship_cfg: Dict[str, Any], target_range_nm: Optional[float]) -> List[CheckResult]:
    w = ship_cfg.get("weapons", {})
    order = ["gun_4_5in", "seacat", "oerlikon_20mm", "gam_bo1_20mm", "exocet_mm38", "corvus_chaff"]
    results: List[CheckResult] = []
    for key in order:
        if key not in w:
            continue
        results.append(check_weapon(key, w[key], target_range_nm))
    # Include any extra/unknown weapons at the end
    for key, wdef in w.items():
        if key not in order:
            results.append(check_weapon(key, wdef, target_range_nm))
    return results

# ---------- CLI for quick test ----------

def _print_table(results: List[CheckResult], rng: Optional[float]) -> None:
    head = f"Target range: {rng:.2f} nm" if rng is not None else "Target range: (none locked)"
    print(head)
    print("WEAPON                READY   AMMO                 RANGE              REASON")
    print("--------------------------------------------------------------------------------")
    for r in results:
        rdy = "READY " if r.ready is True else ("OUT   " if r.ready is False else "N/A   ")
        print(f"{r.name:<20}  {rdy:<6}  {r.ammo_text:<20}  {r.range_text:<16}  {r.reason}")

def main() -> None:
    ap = argparse.ArgumentParser(description="Engagement readiness checker")
    ap.add_argument("--range", type=float, default=None, help="Locked target range in nautical miles")
    args = ap.parse_args()

    ship = _load_ship()
    rng = None if args.range is None else float(args.range)
    results = assess_all(ship, rng)
    _print_table(results, rng)

if __name__ == "__main__":
    main()