#!/usr/bin/env python3
"""
Falklands V2 — Weapons helpers (Step 16a)

What’s here (no side-effects on the engine yet):
- weapons_status(ship_cfg): concise one-line status for the dashboard
- display_name(key): stable pretty names for UI/logs
- get_ammo(ship_cfg, key): read current ammo count(s)
- consume_ammo(ship_cfg, key, n=1): decrement in-memory ship dict (no file I/O)
- format_range(range_def): human-friendly range label

Expected ship.json schema (per weapon key):
- gun_4_5in: { "ammo_he": int, "ammo_illum": int, "effective_max_nm": float? }
- seacat:     { "rounds": int, "range_nm": [min,max] }
- oerlikon_20mm: { "rounds": int, "range_nm": [min,max] }
- gam_bo1_20mm:  { "rounds": int, "range_nm": [min,max] }
- exocet_mm38:   { "rounds": int, "range_nm": [min,max] }
- corvus_chaff:  { "salvoes": int }
"""

from __future__ import annotations
from typing import Dict, Any, Tuple, Optional

# ---- Public API -------------------------------------------------------------

def weapons_status(ship_cfg: Dict[str, Any]) -> str:
    """Return a short, readable summary for the dashboard top line."""
    w = ship_cfg.get("weapons", {})
    parts = []
    # Gun
    if "gun_4_5in" in w:
        g = w["gun_4_5in"]
        parts.append(f"4.5in HE={int(g.get('ammo_he',0))} ILLUM={int(g.get('ammo_illum',0))}")
    # SAM
    if "seacat" in w:
        parts.append(f"SeaCat {int(w['seacat'].get('rounds',0))}")
    # CIWS / 20mm
    if "oerlikon_20mm" in w:
        parts.append(f"Oerlikon {int(w['oerlikon_20mm'].get('rounds',0))}")
    if "gam_bo1_20mm" in w:
        parts.append(f"GAM-BO1 {int(w['gam_bo1_20mm'].get('rounds',0))}")
    # SSM
    if "exocet_mm38" in w:
        parts.append(f"Exocet {int(w['exocet_mm38'].get('rounds',0))}")
    # Chaff
    if "corvus_chaff" in w:
        parts.append(f"Chaff {int(w['corvus_chaff'].get('salvoes',0))}")
    return "WEAPONS: " + (" | ".join(parts) if parts else "(none)")

def display_name(key: str) -> str:
    """Stable pretty names for logs/UI."""
    return {
        "gun_4_5in": "4.5in Mk.8",
        "seacat": "Sea Cat",
        "oerlikon_20mm": "20mm Oerlikon",
        "gam_bo1_20mm": "GAM-BO1 20mm",
        "exocet_mm38": "Exocet MM38",
        "corvus_chaff": "Corvus chaff",
    }.get(key, key)

def get_ammo(ship_cfg: Dict[str, Any], key: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Return (primary, secondary) ammo counts where it makes sense.
    - gun_4_5in → (HE, ILLUM)
    - others    → (rounds, None) or (salvoes, None)
    - unknown   → (None, None)
    """
    w = ship_cfg.get("weapons", {})
    if key not in w:
        return (None, None)
    d = w[key]
    if key == "gun_4_5in":
        return (int(d.get("ammo_he", 0)), int(d.get("ammo_illum", 0)))
    if key in ("seacat", "oerlikon_20mm", "gam_bo1_20mm", "exocet_mm38"):
        return (int(d.get("rounds", 0)), None)
    if key == "corvus_chaff":
        return (int(d.get("salvoes", 0)), None)
    return (None, None)

def consume_ammo(ship_cfg: Dict[str, Any], key: str, n: int = 1, *, illum: bool=False) -> bool:
    """
    Decrement ammo in the given ship_cfg dict (caller persists).
    Returns True if successful (enough ammo), False if blocked.
    - gun_4_5in: decrements HE by default; set illum=True to use illum
    - others: decrements 'rounds' or 'salvoes'
    """
    if n <= 0:
        return True
    w = ship_cfg.setdefault("weapons", {})
    if key not in w:
        return False
    d = w[key]

    if key == "gun_4_5in":
        field = "ammo_illum" if illum else "ammo_he"
        cur = int(d.get(field, 0))
        if cur < n:
            return False
        d[field] = cur - n
        return True

    if key in ("seacat", "oerlikon_20mm", "gam_bo1_20mm", "exocet_mm38"):
        cur = int(d.get("rounds", 0))
        if cur < n:
            return False
        d["rounds"] = cur - n
        return True

    if key == "corvus_chaff":
        cur = int(d.get("salvoes", 0))
        if cur < n:
            return False
        d["salvoes"] = cur - n
        return True

    return False

def format_range(rdef) -> str:
    """Human-friendly range string from float or [min,max]."""
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