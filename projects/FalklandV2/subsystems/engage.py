#!/usr/bin/env python3
"""
Falklands V2 — Engagement / weapon suitability + fire resolution

Public helpers used by webdash.py:
- weapon_valid_for_target(key, wdef, target_type) -> Optional[bool]
- in_range_flag(ship_cfg, key, target_range_nm, target_type) -> Optional[bool]
- fire_once(ship_cfg, FireRequest) -> FireOutcome

Notes
- Stateless: this module never mutates ammo or arming. It only answers
  "is this weapon valid & in range?" and "if fired, did it hit?".
- Range definitions may be a number (max only) or [min,max] list.
- Target types: "ship", "air", "helo" (UI passes "air" for jets/helos).
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple
import json, argparse, sys, random, math

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SHIP = DATA / "ship.json"

# ---------------- Loaders

def _load_ship() -> Dict[str, Any]:
    try:
        return json.loads(SHIP.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"weapons": {}}
    except Exception:
        return {"weapons": {}}

# ---------------- Target validity by weapon

# Canonical valid target sets
_VALID_TARGETS: Dict[str, Tuple[str, ...]] = {
    # Surface gunnery only
    "gun_4_5in": ("ship",),
    # Short-range AA
    "seacat": ("air", "helo"),
    "oerlikon_20mm": ("air", "helo"),
    "gam_bo1_20mm": ("air", "helo"),
    # Anti-ship missile
    "exocet_mm38": ("ship",),
    # Defensive countermeasure; not an offensive shot
    "corvus_chaff": tuple(),
}

def _norm_ttype(target_type: Optional[str]) -> Optional[str]:
    if not target_type:
        return None
    t = str(target_type).lower().strip()
    if t.startswith("air"): return "air"
    if t.startswith("hel"): return "helo"
    if t.startswith("shi"): return "ship"
    return None

def weapon_valid_for_target(key: str, wdef: Dict[str, Any], target_type: Optional[str]) -> Optional[bool]:
    """
    Return True if this weapon may legally engage the given target type.
    False if explicitly invalid. None if undetermined (e.g., no lock).
    """
    if key == "corvus_chaff":
        return False  # not for offensive firing
    t = _norm_ttype(target_type)
    if t is None:
        return None
    valid = _VALID_TARGETS.get(key)
    if valid is None:
        # Unknown weapons: be conservative
        return None
    return t in valid

# ---------------- Range helpers

def _as_range_tuple(rdef: Any) -> Tuple[Optional[float], Optional[float]]:
    """
    Normalize a range definition to (min_nm, max_nm).
    Accepts number (max only) or [min,max].
    """
    if rdef is None:
        return (None, None)
    if isinstance(rdef, (int, float)):
        return (None, float(rdef))
    if isinstance(rdef, list) and len(rdef) == 2:
        lo = None if rdef[0] is None else float(rdef[0])
        hi = None if rdef[1] is None else float(rdef[1])
        return (lo, hi)
    return (None, None)

def _rng_in_def(rng_nm: float, rdef: Any) -> Optional[bool]:
    lo, hi = _as_range_tuple(rdef)
    try:
        if lo is not None and rng_nm < lo:
            return False
        if hi is not None and rng_nm > hi:
            return False
        if lo is None and hi is None:
            return None
        return True
    except Exception:
        return None

def in_range_flag(ship_cfg: Dict[str, Any], key: str, rng_nm: float, target_type: Optional[str]) -> Optional[bool]:
    """
    Combine validity + range for a single weapon.
    Returns True/False/None (None when unknown or not applicable).
    """
    valid = weapon_valid_for_target(key, (ship_cfg.get("weapons") or {}).get(key, {}), target_type)
    if valid is False:
        return None
    if rng_nm is None:
        return None
    w = (ship_cfg.get("weapons") or {}).get(key, {})
    rdef = None
    if key == "gun_4_5in":
        rdef = w.get("effective_max_nm", w.get("range_nm"))
    elif key in ("seacat","oerlikon_20mm","gam_bo1_20mm","exocet_mm38"):
        rdef = w.get("range_nm")
    else:
        rdef = w.get("range_nm")
    return _rng_in_def(float(rng_nm), rdef)

# ---------------- Fire model

@dataclass(frozen=True)
class FireRequest:
    weapon: str
    target_range_nm: Optional[float] = None
    target_type: Optional[str] = None
    test_mode: bool = False  # allows no lock/range if True

@dataclass(frozen=True)
class FireOutcome:
    ok: bool
    reason: str = ""
    in_range: Optional[bool] = None
    pk_used: float = 0.0
    hit: bool = False

def _tri_peak(x: float, x0: float, x1: float, peak: float, min_edge: float) -> float:
    """
    Simple triangle profile between [x0,x1] peaking at midpoint.
    Returns min_edge outside range.
    """
    if x0 is None or x1 is None or x0 >= x1:
        return min_edge
    if x <= x0 or x >= x1:
        return min_edge
    mid = 0.5 * (x0 + x1)
    if x <= mid:
        # rise from edge to peak
        return min_edge + (peak - min_edge) * ((x - x0) / (mid - x0))
    else:
        # fall back to edge
        return min_edge + (peak - min_edge) * ((x1 - x) / (x1 - mid))

def _pk_for_shot(key: str, wdef: Dict[str, Any], rng_nm: Optional[float], ttype: Optional[str]) -> Tuple[bool, float, str]:
    """
    Decide if the shot is allowed and compute Pk.
    Returns (allowed, pk, reason_if_denied).
    """
    if key == "corvus_chaff":
        return (False, 0.0, "countermeasure only")

    # Test mode is handled by caller; here we assume lock present.
    ttype_n = _norm_ttype(ttype)
    valid = weapon_valid_for_target(key, wdef, ttype_n)
    if valid is False:
        return (False, 0.0, "invalid target")
    if rng_nm is None:
        return (False, 0.0, "no range")

    # Range definition
    if key == "gun_4_5in":
        rdef = wdef.get("effective_max_nm", wdef.get("range_nm"))
    else:
        rdef = wdef.get("range_nm")
    in_rng = _rng_in_def(float(rng_nm), rdef)
    if in_rng is False:
        return (False, 0.0, "out of range")
    if in_rng is None:
        return (False, 0.0, "range undefined")

    # Probability-of-kill by weapon
    if key == "seacat":
        # Envelope ~1–3 nm, peak ~0.25 mid-window, edges ~0.07
        lo, hi = _as_range_tuple(rdef); lo = lo if lo is not None else 1.0; hi = hi if hi is not None else 3.0
        pk = _tri_peak(float(rng_nm), lo, hi, peak=0.25, min_edge=0.07)
    elif key in ("oerlikon_20mm", "gam_bo1_20mm"):
        # 0.3–2.5 nm, close-in peak ~0.15, edges ~0.05
        lo, hi = _as_range_tuple(rdef); lo = lo if lo is not None else 0.3; hi = hi if hi is not None else 2.5
        pk = _tri_peak(float(rng_nm), lo, hi, peak=0.15, min_edge=0.05)
    elif key == "gun_4_5in":
        # Surface gunnery single "salvo" abstraction, modest pk
        pk = 0.20
    elif key == "exocet_mm38":
        # Anti-ship missile; decent pk in envelope
        pk = 0.70
    else:
        pk = 0.10  # fallback for unknowns

    # Clamp
    pk = max(0.0, min(1.0, pk))
    return (True, pk, "")

def fire_once(ship_cfg: Dict[str, Any], req: FireRequest) -> FireOutcome:
    """
    Resolve a single shot. Stateless: ammo/arming are handled elsewhere.
    - If test_mode=True: always allowed (assumes caller checked ammo & arming).
    - Otherwise: requires valid target_type and in-envelope range.
    """
    w = (ship_cfg.get("weapons") or {})
    wdef = w.get(req.weapon or "")
    if not wdef:
        return FireOutcome(ok=False, reason="unknown weapon")

    if req.test_mode:
        # Training shot: pick a small pk just to produce MISS most times
        pk = 0.05
        hit = (random.random() < pk)
        return FireOutcome(ok=True, reason="test", in_range=None, pk_used=pk, hit=hit)

    # Normal fire
    allowed, pk, why = _pk_for_shot(req.weapon, wdef, req.target_range_nm, req.target_type)
    if not allowed:
        return FireOutcome(ok=False, reason=why, in_range=_rng_in_def(float(req.target_range_nm or 0.0), wdef.get("range_nm")) if req.target_range_nm is not None else None, pk_used=0.0, hit=False)

    hit = (random.random() < pk)
    return FireOutcome(ok=True, reason="fired", in_range=True, pk_used=pk, hit=hit)

# ---------------- (Optional) CLI for quick checks

@dataclass(frozen=True)
class CheckResult:
    key: str
    name: str
    valid: Optional[bool]
    in_range: Optional[bool]
    range_text: str

def _fmt_range(rdef: Any) -> str:
    lo, hi = _as_range_tuple(rdef)
    parts: List[str] = []
    if lo is not None: parts.append(f"≥{lo:.1f}")
    if hi is not None: parts.append(f"≤{hi:.1f}")
    return ("–".join(parts) + " nm") if parts else "—"

def _collect_readiness(ship_cfg: Dict[str, Any], rng: Optional[float], ttype: Optional[str]) -> List[CheckResult]:
    order = ["gun_4_5in","seacat","oerlikon_20mm","gam_bo1_20mm","exocet_mm38","corvus_chaff"]
    out: List[CheckResult] = []
    for k in order:
        wdef = (ship_cfg.get("weapons") or {}).get(k)
        if not wdef: continue
        valid = weapon_valid_for_target(k, wdef, ttype)
        if k == "gun_4_5in":
            rdef = wdef.get("effective_max_nm", wdef.get("range_nm"))
        else:
            rdef = wdef.get("range_nm")
        inrng = None if rng is None else _rng_in_def(float(rng), rdef)
        out.append(CheckResult(key=k, name=k, valid=valid, in_range=inrng, range_text=_fmt_range(rdef)))
    return out

def main() -> None:
    ap = argparse.ArgumentParser(description="engage.py sanity")
    ap.add_argument("--range", type=float, default=None, help="Target range in nm")
    ap.add_argument("--target", type=str, default=None, help="Target type: ship|air|helo")
    args = ap.parse_args()

    ship = _load_ship()
    res = _collect_readiness(ship, args.range, args.target)
    print(f"Target: type={args.target!r} range={args.range if args.range is not None else '(none)'}")
    print("WEAPON              VALID   IN-RANGE   RANGE")
    print("----------------------------------------------------")
    for r in res:
        v = "T" if r.valid is True else ("F" if r.valid is False else "-")
        ir = "T" if r.in_range is True else ("F" if r.in_range is False else "-")
        print(f"{r.name:<18}  {v:<6}  {ir:<8}  {r.range_text}")

if __name__ == "__main__":
    main()