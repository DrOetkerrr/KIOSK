#!/usr/bin/env python3
"""
Engagement brain (stable)
- Computes per-weapon readiness against a locked target (range + validity + ammo).
- Tracks arming (5s) per weapon in Engine.state['arming'].
- Mutates ship ammo on fire/test and returns a simple outcome.
- Provides a single "summarize()" surface for the UI.

Target classes:
  air      → aircraft & helicopters
  surface  → everything else (ships, boats, ground)

Validity matrix (design-intent):
  4.5in gun        → surface only
  Sea Dart SAM     → air only
  Oerlikon 20mm    → air only (very short)
  GAM-BO1 20mm     → air only (short)
  Exocet MM38      → surface only
  Corvus chaff     → not range-gated; always "valid" but still needs ammo
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import time

# ---------- helpers: target typing ----------

def _target_class(ttype: Optional[str]) -> str:
    t = (ttype or "").strip().lower()
    if not t:
        return "surface"  # be conservative (only Sea Dart will then show invalid)
    if "air" in t or "helicopter" in t or t == "helo":
        return "air"
    return "surface"

# ---------- range helpers ----------

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

def _in_range_flag(rdef: Any, rng_nm: Optional[float]) -> Optional[bool]:
    if rng_nm is None or rdef is None:
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

# ---------- validity matrix ----------

def weapon_valid_for_target(key: str, target_type: Optional[str]) -> bool:
    cls_ = _target_class(target_type)
    k = key.lower()
    if k in ("gun_4_5in", "exocet_mm38"):
        return cls_ == "surface"
    if k in ("seacat", "oerlikon_20mm", "gam_bo1_20mm"):
        return cls_ == "air"
    if k == "corvus_chaff":
        return True
    # Unknown weapons default to safe/false to avoid accidental green lights
    return False

# ---------- ammo accessors ----------

def _weapon_ammo_text(key: str, wdef: Dict[str, Any]) -> Tuple[str, bool, int]:
    """Return (display, ammo_ok, numeric_primary)"""
    k = key.lower()
    if k == "gun_4_5in":
        he = int(wdef.get("ammo_he", 0))
        il = int(wdef.get("ammo_illum", 0))
        return f"HE={he} ILLUM={il}", he > 0, he
    if k in ("seacat", "oerlikon_20mm", "gam_bo1_20mm", "exocet_mm38"):
        r = int(wdef.get("rounds", 0))
        return str(r), r > 0, r
    if k == "corvus_chaff":
        s = int(wdef.get("salvoes", 0))
        return str(s), s > 0, s
    return "?", False, 0

def _weapon_range_def(key: str, wdef: Dict[str, Any]) -> Any:
    k = key.lower()
    if k == "gun_4_5in":
        return wdef.get("effective_max_nm", wdef.get("range_nm"))
    return wdef.get("range_nm")

# ---------- public row structure ----------

@dataclass(frozen=True)
class Row:
    key: str
    name: str
    ammo_text: str
    range_text: str
    valid: bool
    in_range: Optional[bool]
    ready: Optional[bool]
    reason: str

# ---------- summarize ----------

def summarize(ship_cfg: Dict[str, Any], target: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return rows for UI. `target` may be None, or { range_nm: float, type: str }."""
    weapons = ship_cfg.get("weapons", {})
    order = ["gun_4_5in", "seacat", "oerlikon_20mm", "gam_bo1_20mm", "exocet_mm38", "corvus_chaff"]

    out: List[Dict[str, Any]] = []
    rng_nm = (target or {}).get("range_nm")
    ttype = (target or {}).get("type")

    for key in order:
        if key not in weapons:
            continue
        wdef = weapons[key]
        name = wdef.get("name") or {
            "gun_4_5in":"4.5in Mk.8",
            "seacat":"Sea Dart SAM",
            "oerlikon_20mm":"20mm Oerlikon",
            "gam_bo1_20mm":"20mm GAM-BO1 (twin)",
            "exocet_mm38":"MM38 Exocet",
            "corvus_chaff":"Corvus chaff",
        }.get(key, key)

        ammo_text, ammo_ok, _n = _weapon_ammo_text(key, wdef)
        rdef = _weapon_range_def(key, wdef)
        rtxt = _fmt_range(rdef)

        if key == "corvus_chaff":
            # Not range-gated, but needs ammo
            ready = True if ammo_ok else False
            reason = "ready" if ready else "no ammo"
            out.append(dict(
                key=key, name=name, ammo_text=ammo_text, range_text="—",
                valid=True, in_range=None, ready=ready, reason=reason
            ))
            continue

        valid = weapon_valid_for_target(key, ttype)
        inrng = _in_range_flag(rdef, rng_nm)

        if rng_nm is None:
            # No lock → show N/A but keep validity visible by reason
            out.append(dict(
                key=key, name=name, ammo_text=ammo_text, range_text=rtxt,
                valid=valid, in_range=None, ready=None, reason="no locked target"
            ))
            continue

        if not valid:
            out.append(dict(
                key=key, name=name, ammo_text=ammo_text, range_text=rtxt,
                valid=False, in_range=inrng, ready=False, reason="invalid vs air" if _target_class(ttype)=="air" else "invalid vs surface"
            ))
            continue

        if not ammo_ok:
            out.append(dict(
                key=key, name=name, ammo_text=ammo_text, range_text=rtxt,
                valid=True, in_range=inrng, ready=False, reason="no ammo"
            ))
            continue

        if inrng is False:
            out.append(dict(
                key=key, name=name, ammo_text=ammo_text, range_text=rtxt,
                valid=True, in_range=False, ready=False, reason="out of range"
            ))
            continue

        if inrng is None:
            out.append(dict(
                key=key, name=name, ammo_text=ammo_text, range_text=rtxt,
                valid=True, in_range=None, ready=None, reason="range undefined"
            ))
            continue

        # All green
        out.append(dict(
            key=key, name=name, ammo_text=ammo_text, range_text=rtxt,
            valid=True, in_range=True, ready=True, reason="ready"
        ))

    # include any extra weapons not in order
    for key, wdef in weapons.items():
        if any(r["key"] == key for r in out): continue
        name = wdef.get("name", key)
        ammo_text, ammo_ok, _n = _weapon_ammo_text(key, wdef)
        rdef = _weapon_range_def(key, wdef)
        rtxt = _fmt_range(rdef)
        valid = weapon_valid_for_target(key, (target or {}).get("type"))
        inrng = _in_range_flag(rdef, (target or {}).get("range_nm"))
        # simple rule set
        ready = (ammo_ok and valid and (inrng or (inrng is None)))
        out.append(dict(
            key=key, name=name, ammo_text=ammo_text, range_text=rtxt,
            valid=valid, in_range=inrng, ready=(True if ready else False),
            reason=("ready" if ready else "blocked")
        ))
    return out

# ---------- arming (5s) ----------

ARM_TIME_S = 5

def arm_start(state: Dict[str, Any], weapon_key: str, now: float) -> None:
    arm = state.setdefault("arming", {})
    rec = arm.get(weapon_key, {"armed": False, "arming_until": 0})
    rec["armed"] = False
    rec["arming_until"] = now + ARM_TIME_S
    arm[weapon_key] = rec

def arm_status(state: Dict[str, Any], weapon_key: str, now: float) -> Dict[str, Any]:
    rec = state.setdefault("arming", {}).get(weapon_key, {"armed": False, "arming_until": 0})
    if rec.get("armed"):
        return {"armed": True, "arming_s": 0}
    until = float(rec.get("arming_until", 0))
    if now >= until and until > 0:
        rec["armed"] = True
        rec["arming_until"] = 0
        state.setdefault("arming", {})[weapon_key] = rec
        return {"armed": True, "arming_s": 0}
    left = max(0, int(round(until - now))) if until > 0 else 0
    return {"armed": False, "arming_s": left}

# ---------- firing ----------

@dataclass
class FireRequest:
    weapon: str
    target_range_nm: Optional[float]
    target_type: Optional[str]
    mode: str = "fire"   # "fire" | "test"

def _dec_ammo(wdef: Dict[str, Any], field: str, cnt: int = 1) -> int:
    n = int(wdef.get(field, 0))
    n = max(0, n - cnt)
    wdef[field] = n
    return n

def fire_once(ship_cfg: Dict[str, Any], req: FireRequest) -> Dict[str, Any]:
    """
    Returns: {"ok": bool, "message": str, "ammo_after": int}
    - In 'test' mode, ignores range/validity, but still requires arming (enforced by caller).
    - In 'fire' mode, enforces range + validity + ammo.
    """
    w = ship_cfg.get("weapons", {})
    key = req.weapon
    if key not in w:
        return {"ok": False, "message": f"unknown weapon {key}"}
    wdef = w[key]

    # Ammo fields and validity
    ammo_text, ammo_ok, _n = _weapon_ammo_text(key, wdef)
    if not ammo_ok:
        return {"ok": False, "message": "no ammo"}

    if req.mode != "test":
        # enforce validity + range
        if not weapon_valid_for_target(key, req.target_type):
            return {"ok": False, "message": "invalid target"}
        rdef = _weapon_range_def(key, wdef)
        inrng = _in_range_flag(rdef, req.target_range_nm)
        if inrng is False or inrng is None:
            return {"ok": False, "message": "out of range"}

    # Decrement ammo (one unit)
    key_l = key.lower()
    if key_l == "gun_4_5in":
        after = _dec_ammo(wdef, "ammo_he", 1)
    elif key_l in ("seacat", "oerlikon_20mm", "gam_bo1_20mm", "exocet_mm38"):
        after = _dec_ammo(wdef, "rounds", 1)
    elif key_l == "corvus_chaff":
        after = _dec_ammo(wdef, "salvoes", 1)
    else:
        # unknown → try 'rounds'
        after = _dec_ammo(wdef, "rounds", 1)

    return {"ok": True, "message": "FIRED" if req.mode == "fire" else "TEST FIRE", "ammo_after": after}
