# projects/falklands/systems/weapons.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Any, Tuple
import json
import math
import time

def _norm(s: str) -> str:
    s = (s or "").replace("_", " ").strip().lower()
    return " ".join(s.split())

def _load_json(p: Path, fallback):
    try:
        return json.loads(p.read_text())
    except Exception:
        return fallback

@dataclass
class WeaponsSystem:
    """
    Weapons manager with two modes:
      - combat fire: /weapons fire target=<id>  (needs target + checks range/type + PK)
      - test fire:  /weapons test [name=<system>] [rounds=<n>] (no target, consumes ammo)
    Sounds are not played here; we only return text. The caller (run_bridge) can decide to play a launch SFX.
    """
    data_dir: Path
    inventory: Dict[str, int] = field(init=False)
    db: Dict[str, Any] = field(init=False)
    armed: bool = False
    selected: Optional[str] = None   # display name (matches keys in inventory)

    def __post_init__(self):
        inv_path = self.data_dir / "weapon_loadout.json"
        db_path  = self.data_dir / "weapons_db.json"
        # sensible defaults if files are missing
        self.inventory = _load_json(inv_path, {
            "4.5-inch gun": 475,
            "20 mm cannon": 2000,
            "Sea Cat": 22,
            "Exocet": 4,
            "STWS Mk46": 6,
            "Monitor only": 0,
            "None": 0,
        })
        self.db = _load_json(db_path, {
            # minimal fallback schema
            "Sea Cat": {"min_nm": 1.0, "max_nm": 5.0, "roles": ["air"], "launch_sfx": "SeaCatLaunch.m4a"},
            "Exocet":  {"min_nm": 7.0, "max_nm": 23.0, "roles": ["surface"], "launch_sfx": "exocet.mp3"},
            "4.5-inch gun": {"min_nm": 1.0, "max_nm": 12.0, "roles": ["surface","shore"]},
            "20 mm cannon": {"min_nm": 0.0, "max_nm": 2.0, "roles": ["close"]},
            "STWS Mk46": {"min_nm": 0.5, "max_nm": 8.0, "roles": ["sub"]},
        })

    # ---- helpers ----
    def _find_display_name(self, name_in: Optional[str]) -> Optional[str]:
        if not name_in:
            return None
        want = _norm(name_in)
        for disp in self.inventory.keys():
            if _norm(disp) == want:
                return disp
        return None

    def _grid_to_nm(self, a: Tuple[float,float], b: Tuple[float,float], cell_nm: float) -> float:
        dx = (b[0] - a[0]) * cell_nm
        dy = (b[1] - a[1]) * cell_nm
        return math.sqrt(dx*dx + dy*dy)

    def _target_role(self, ttype: str) -> str:
        t = (ttype or "").lower()
        if any(k in t for k in ["jet","aircraft","helicopter","bomber","fighter","airliner","drone","balloon"]):
            return "air"
        if any(k in t for k in ["sub", "submarine"]):
            return "sub"
        if any(k in t for k in ["tanker","freighter","destroyer","frigate","vessel","boat","ship","merchant","cutter"]):
            return "surface"
        return "surface"  # safe default

    def _pk_vs_range(self, dist_nm: float, winfo: Dict[str, Any]) -> float:
        """Simple probability curve: 0 at max+20%, peak near min, clipped 0..1."""
        min_nm = float(winfo.get("min_nm", 0.0))
        max_nm = float(winfo.get("max_nm", 0.0))
        if dist_nm < min_nm:
            # too close can also be bad for some systems; keep it modest
            return 0.20
        if dist_nm > max_nm * 1.2:
            return 0.0
        # scale: at min → 0.8, at max → 0.3, linear between
        if max_nm <= min_nm:
            return 0.5
        t = (dist_nm - min_nm) / max(1e-6, (max_nm - min_nm))
        pk = 0.8*(1.0 - t) + 0.3*t
        return max(0.0, min(1.0, pk))

    # ---- public API used by Engine ----
    def show(self, params: Dict[str, Any]) -> str:
        inv = ", ".join(f"{k}({v})" for k, v in self.inventory.items())
        sel = self.selected if self.selected else "None"
        return f"Weapons: {'ARMED' if self.armed else 'SAFE'}; selected={sel}; inventory=[{inv}]"

    def arm(self, params: Dict[str, Any]) -> str:
        self.armed = True
        return "Weapons: ARMED"

    def safe(self, params: Dict[str, Any]) -> str:
        self.armed = False
        return "Weapons: SAFE"

    def select(self, params: Dict[str, Any]) -> str:
        name = params.get("name") or params.get("system")
        disp = self._find_display_name(name)
        if not disp:
            return f"Weapons: '{name}' not in inventory {list(self.inventory.keys())}"
        self.selected = disp
        return f"Weapons: selected={disp}"

    def test(self, params: Dict[str, Any]) -> str:
        """
        Test fire without a target. If name=... is provided, use that; otherwise use selected.
        Consumes 1 round (or 'rounds' if provided). No hit/miss logic. Pure drill.
        """
        if not self.armed:
            return "Weapons: cannot test while SAFE"
        name = params.get("name")
        disp = self._find_display_name(name) if name else self.selected
        if not disp:
            return "Weapons: no system selected (use /weapons select name=<system>)"
        rounds = max(1, int(params.get("rounds", 1)))
        have = int(self.inventory.get(disp, 0))
        if have < rounds:
            return f"Weapons: insufficient {disp} ammo for test (have {have}, need {rounds})"
        self.inventory[disp] = have - rounds
        # We *hint* launch SFX by embedding a tag the caller can parse, but it’s safe to ignore.
        sfx = self.db.get(disp, {}).get("launch_sfx")
        tag = f" [SFX:{sfx}]" if sfx else ""
        return f"Test fire: {disp} x{rounds} expended; remaining={self.inventory[disp]}.{tag}"

    def fire(self, params: Dict[str, Any], state: Optional[Dict[str, Any]]=None, contacts: Optional[Dict[str, Any]]=None) -> str:
        """
        Combat fire at a target ID. Checks arm/safe, selection, target role, range, PK; consumes ammo.
        Returns a short outcome message. Does not play audio here.
        """
        if not self.armed:
            return "Weapons: cannot fire while SAFE"
        if not self.selected:
            return "Weapons: no system selected"
        disp = self.selected
        if self.inventory.get(disp, 0) <= 0:
            return f"Weapons: out of {disp}"

        target_id = params.get("target")
        if not target_id:
            return "Weapons: fire requires target=<id> (or use /weapons test for drills)"

        if not state or not contacts:
            return "Weapons: missing state/contacts for firing solution"

        tgt = contacts.get(target_id)
        if not tgt:
            return f"Weapons: target '{target_id}' not found"

        # geometry
        cell_nm = float(state.get("CELL_NM", 4.0))
        ship = state.get("ship", {})
        ship_xy = (float(ship.get("col", 50)), float(ship.get("row", 50)))
        # allowed grid formats "x-y"
        grid = tgt.get("grid")
        try:
            cx, cy = grid.split("-")
            tgt_xy = (float(cx), float(cy))
        except Exception:
            return "Weapons: target has invalid grid"

        dist = self._grid_to_nm(ship_xy, tgt_xy, cell_nm)

        # weapon data
        winfo = self.db.get(disp, {})
        min_nm = float(winfo.get("min_nm", 0.0))
        max_nm = float(winfo.get("max_nm", 0.0))
        role = self._target_role(tgt.get("type",""))
        allowed_roles = winfo.get("roles", ["surface"])

        if role not in allowed_roles:
            return f"Weapons: wrong weapon for target type ({tgt.get('type')}); {disp} roles={allowed_roles}"
        if dist < min_nm or dist > max_nm:
            return f"Weapons: {disp} out of range ({dist:.1f} NM; allowed {min_nm:.1f}-{max_nm:.1f} NM)"

        # compute PK and consume ammo
        pk = self._pk_vs_range(dist, winfo)
        self.inventory[disp] -= 1

        # caller can decide to sleep for "flight time"; we just hint a notional time
        flight_time_s = max(1.0, min(20.0, dist / max(0.1, (max_nm/15.0))))  # rough, capped

        # hint at launch SFX
        sfx = winfo.get("launch_sfx")
        sfx_tag = f" [SFX:{sfx}]" if sfx else ""

        # roll
        hit = (pk >= 0.5)  # deterministic threshold (you can randomize later if desired)
        if hit:
            return (f"FIRE: {disp} at {target_id} ({dist:.1f} NM){sfx_tag}. "
                    f"Flight ~{flight_time_s:.1f}s. Impact: TARGET DESTROYED. "
                    f"Remaining {disp}={self.inventory[disp]}")
        else:
            return (f"FIRE: {disp} at {target_id} ({dist:.1f} NM){sfx_tag}. "
                    f"Flight ~{flight_time_s:.1f}s. Impact: MISS. "
                    f"Remaining {disp}={self.inventory[disp]}")