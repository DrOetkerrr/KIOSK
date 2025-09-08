# subsystems/hermes_cap.py
"""
Hermes CAP subsystem — behaves like an off-board "weapon with reach".
- Reads config from data/cap_config.json ("cap_config" object).
- Exposes readiness(), request_cap_to_cell(distance_nm=...), tick(), and snapshot().
- Engine supplies distance_nm (Hermes → target) so this module stays grid-agnostic.
"""

from __future__ import annotations
import time, json
from pathlib import Path
from typing import Any, Dict, List, Optional

def _read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))

class CAPMission:
    """State machine: queued -> airborne -> onstation -> rtb -> complete."""
    def __init__(self, mission_id: int, target_cell: str, cfg: Dict[str, Any], *, now: float, distance_nm: float):
        self.id = mission_id
        self.target_cell = target_cell
        self.status = "queued"
        self.ts: Dict[str, float] = {"created": now}
        self.cfg = cfg

        # Static params
        self.deck_cycle_s = int(cfg.get("deck_cycle_per_pair_s", 180))
        self.onstation_s = int(cfg.get("default_onstation_min", 20)) * 60
        self.bingo_rtb_buffer_s = int(cfg.get("bingo_rtb_buffer_min", 4)) * 60
        self.cruise_speed_kts = float(cfg.get("cruise_speed_kts", 420))
        self.station_radius_nm = float(cfg.get("station_radius_nm", 5))

        # Transit times from distance (one way)
        # t_hours = nm / kts  -> seconds
        one_leg_s = int((distance_nm / max(self.cruise_speed_kts, 1.0)) * 3600.0)
        self.outbound_s = max(1, one_leg_s)
        self.inbound_s = max(1, one_leg_s)

        # Derived timeline (filled in on tick)
        self.ts["launch"] = now            # simplified: launch immediately on approve
        self.ts["eta_onstation"] = now + self.deck_cycle_s + self.outbound_s
        self.ts["etd_rtb"] = None          # set when on-station starts
        self.ts["eta_recovery"] = None     # set when RTB starts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "target_cell": self.target_cell,
            "status": self.status,
            "station_radius_nm": self.station_radius_nm,
            "timestamps": self.ts,
        }

class HermesCAP:
    """Manages pool/cooldowns and missions; looks like a long-reach weapon to callers."""
    def __init__(self, data_path: Path):
        self.data_path = data_path
        self.cfg = self._load_cfg()
        self.airframe_pool_total = int(self.cfg.get("airframe_pool_total", 8))
        self.ready_pairs_max = int(self.cfg.get("max_ready_pairs", 2))
        self.ready_pairs = self.ready_pairs_max
        self.pair_rearm_refuel_s = int(self.cfg.get("pair_rearm_refuel_min", 25)) * 60
        self.scramble_cooldown_s = int(self.cfg.get("scramble_cooldown_min", 10)) * 60
        self.min_launch_interval_s = int(self.cfg.get("min_launch_interval_s", 30))
        self.last_scramble: float = 0.0
        self.missions: List[CAPMission] = []
        self._next_id = 1

    # ---------- config
    def _load_cfg(self) -> Dict[str, Any]:
        f = self.data_path / "cap_config.json"
        if not f.exists():
            return {}
        try:
            return _read_json(f).get("cap_config", {})
        except Exception:
            return {}

    # ---------- weapon-like surface
    def readiness(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Return whether 'Hermes CAP' is READY like a weapon: available pair, airframes, cooldown clear."""
        t = now or time.time()
        cd_left = max(0, int(self.scramble_cooldown_s - (t - self.last_scramble)))
        return {
            "available": (self.ready_pairs >= 1 and self.airframe_pool_total >= 2 and cd_left == 0),
            "ready_pairs": self.ready_pairs,
            "airframes": self.airframe_pool_total,
            "cooldown_s": cd_left,
            "station_radius_nm": float(self.cfg.get("station_radius_nm", 5))
        }

    def request_cap_to_cell(self, target_cell: str, *, distance_nm: float, now: Optional[float] = None) -> Dict[str, Any]:
        """
        Fire-like call: try to 'use' Hermes CAP at a grid cell.
        distance_nm = Hermes→target one-way distance (Engine computes).
        """
        t = now or time.time()

        # Enforce launch spacing/cooldown
        if (t - self.last_scramble) < self.min_launch_interval_s:
            return {"ok": False, "message": "Deck cycle in progress"}
        if (t - self.last_scramble) < self.scramble_cooldown_s:
            return {"ok": False, "message": "Scramble cooldown active"}
        if self.ready_pairs < 1:
            return {"ok": False, "message": "No ready pairs on deck"}
        if self.airframe_pool_total < 2:
            return {"ok": False, "message": "Insufficient airframes"}

        # Approve and launch
        m = CAPMission(self._next_id, target_cell, self.cfg, now=t, distance_nm=float(distance_nm))
        self._next_id += 1
        self.missions.append(m)
        self.ready_pairs -= 1
        self.airframe_pool_total -= 2
        self.last_scramble = t
        m.status = "airborne"
        return {
            "ok": True,
            "message": f"Hermes: CAP pair launching to {target_cell}",
            "mission": m.to_dict()
        }

    def tick(self, now: Optional[float] = None) -> None:
        """Advance mission states and recycle pairs after recovery (rearm/refuel)."""
        t = now or time.time()
        for m in self.missions:
            # queued shouldn't really happen with current immediate launch; keep logic resilient
            if m.status == "queued":
                if t >= m.ts["launch"] + m.deck_cycle_s:
                    m.status = "airborne"
            elif m.status == "airborne":
                if t >= m.ts["eta_onstation"]:
                    m.status = "onstation"
                    m.ts["onstation"] = t
                    m.ts["etd_rtb"] = t + m.onstation_s
            elif m.status == "onstation":
                if t >= (m.ts.get("etd_rtb") or t):
                    m.status = "rtb"
                    m.ts["rtb"] = t
                    m.ts["eta_recovery"] = t + m.inbound_s
            elif m.status == "rtb":
                if t >= (m.ts.get("eta_recovery") or t):
                    m.status = "recovering"
                    m.ts["recovering"] = t
                    m.ts["ready_again"] = t + self.pair_rearm_refuel_s
            elif m.status == "recovering":
                if t >= (m.ts.get("ready_again") or t):
                    m.status = "complete"
                    m.ts["complete"] = t
                    # recycle one ready pair (airframes remain reduced unless you script replacements)
                    self.ready_pairs = min(self.ready_pairs + 1, self.ready_pairs_max)

        # Optional pruning: keep the last few completed missions
        if len(self.missions) > 12:
            self.missions = [m for m in self.missions if m.status != "complete"][-12:]

    # ---------- effects surface for Engine (to hook into spawn/defence)
    def current_effects(self) -> Dict[str, Any]:
        """Return the strongest active CAP effects (no stacking)."""
        eff = self.cfg.get("effects", {}) if self.cfg else {}
        # If any mission is on-station, effects apply in that radius at its target cell.
        onst = [m for m in self.missions if m.status == "onstation"]
        return {
            "active": len(onst) > 0,
            "stations": [
                {
                    "target_cell": m.target_cell,
                    "radius_nm": m.station_radius_nm,
                    "effects": eff
                } for m in onst
            ]
        }

    # ---------- UI helpers
    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        r = self.readiness(now=now)
        return {
            "readiness": r,
            "missions": [m.to_dict() for m in self.missions]
        }