#!/usr/bin/env python3
"""
Weapons subsystem (Step 11) — status only.

Single responsibility (for now):
- Load ship weapons config from data/ship.json
- Produce a concise, readable status line (ammo + ranges, cell-first style not relevant here)

No engine dependency. Safe to run standalone for a quick check.
Later, this module will grow: arming, firing, flight-time, hit resolution.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
import json

ROOT = Path(__file__).resolve().parents[1]   # .../FalklandV2
DATA = ROOT / "data"

def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

@dataclass(frozen=True)
class WeaponView:
    name: str
    info: str  # short text: counts + range

def _fmt_range(r):
    """Range can be single float (max nm) or [min,max]."""
    if r is None:
        return ""
    if isinstance(r, (int, float)):
        return f"≤{float(r):.1f} nm"
    if isinstance(r, (list, tuple)) and len(r) == 2:
        lo, hi = r
        parts = []
        if lo is not None:
            parts.append(f"≥{float(lo):.1f}")
        if hi is not None:
            parts.append(f"≤{float(hi):.1f}")
        return "–".join(parts) + " nm" if parts else ""
    return ""

def weapons_status(ship_cfg: Dict[str, Any]) -> str:
    """
    Returns a single-line concise status like:
    'WEAPONS: 4.5in HE=550 ILLUM=100 (≤8.0 nm) | Sea Cat ROUNDS=35 (≥1.0–≤3.0 nm) | Exocet ROUNDS=4 (≥3.0–≤22.0 nm) | Corvus CHAFF=15 | 20mm Oerlikon ROUNDS=5000 (≥0.3–≤0.5 nm)'
    """
    w = ship_cfg.get("weapons", {})
    chunks = []

    # 4.5" gun
    if "gun_4_5in" in w:
        g = w["gun_4_5in"]
        rng = _fmt_range(g.get("effective_max_nm", g.get("range_nm")))
        chunks.append(f"4.5in HE={g.get('ammo_he',0)} ILLUM={g.get('ammo_illum',0)} ({rng})" if rng else
                      f"4.5in HE={g.get('ammo_he',0)} ILLUM={g.get('ammo_illum',0)}")

    # Sea Cat
    if "seacat" in w:
        sc = w["seacat"]
        rng = _fmt_range(sc.get("range_nm"))
        chunks.append(f"Sea Cat ROUNDS={sc.get('rounds',0)} ({rng})" if rng else
                      f"Sea Cat ROUNDS={sc.get('rounds',0)}")

    # Oerlikon 20mm
    if "oerlikon_20mm" in w:
        o = w["oerlikon_20mm"]
        rng = _fmt_range(o.get("range_nm"))
        chunks.append(f"20mm Oerlikon ROUNDS={o.get('rounds',0)} ({rng})" if rng else
                      f"20mm Oerlikon ROUNDS={o.get('rounds',0)}")

    # GAM-BO1 20mm
    if "gam_bo1_20mm" in w:
        g2 = w["gam_bo1_20mm"]
        rng = _fmt_range(g2.get("range_nm"))
        chunks.append(f"GAM-BO1 20mm ({rng})" if rng else "GAM-BO1 20mm")

    # Exocet MM38
    if "exocet_mm38" in w:
        ex = w["exocet_mm38"]
        rng = _fmt_range(ex.get("range_nm"))
        chunks.append(f"Exocet MM38 ROUNDS={ex.get('rounds',0)} ({rng})" if rng else
                      f"Exocet MM38 ROUNDS={ex.get('rounds',0)}")

    # Corvus chaff
    if "corvus_chaff" in w:
        ch = w["corvus_chaff"]
        chunks.append(f"Corvus CHAFF={ch.get('salvoes',0)}")

    if not chunks:
        return "WEAPONS: (none configured)"
    return "WEAPONS: " + " | ".join(chunks)

# ---------- Self-test / demo ----------

def _demo() -> None:
    try:
        ship = _load_json(DATA / "ship.json")
    except FileNotFoundError:
        print("No data/ship.json found; create it first (from earlier step).")
        return
    name = ship.get("name", "Own Ship")
    klass = ship.get("class", "")
    head = f"{name} ({klass})" if klass else name
    print(head)
    print(weapons_status(ship))

if __name__ == "__main__":
    _demo()