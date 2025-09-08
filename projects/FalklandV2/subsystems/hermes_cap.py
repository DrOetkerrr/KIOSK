"""
Hermes CAP subsystem — behaves like an off-board "weapon with reach".
Now models Sea Harrier pairs with 2× Sidewinder missiles and simple range-based Pk.

Public surface:
- readiness(now=None) -> basic availability
- request_cap_to_cell(target_cell, *, distance_nm, now=None) -> launch a mission
- tick(now=None) -> advance mission states and recycle pairs
- auto_engage(distance_nm, locked_target_id, now=None) -> if on-station and in range, fire missiles
- snapshot(now=None) -> UI view
- current_effects() -> (unchanged placeholder hook for engine-side effects)
"""

from __future__ import annotations
import time, json, random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

def _read_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t

def _interp(x: float, pts: List[Tuple[float, float]]) -> float:
    """Piecewise-linear interpolation of y over sorted (x,y) pts."""
    pts = sorted(pts, key=lambda p: p[0])
    if x <= pts[0][0]: return pts[0][1]
    if x >= pts[-1][0]: return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        if x0 <= x <= x1:
            t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
            return _lerp(y0, y1, t)
    return pts[-1][1]

class CAPMission:
    """State machine: queued -> airborne -> onstation -> rtb -> recovering -> complete."""
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

        # Simple weapons loadout for a pair: two AIM-9 total (not per-airframe)
        wcfg = (cfg.get("weapons") or {}).get("aim9", {})
        self.missiles_total = int(wcfg.get("missiles_total", 2))
        self.missiles_left = self.missiles_total
        self.engagement_cooldown_s = int(wcfg.get("engagement_cooldown_s", 5))
        self.last_engagement_s: float = 0.0
        self.last_engagement: Optional[Dict[str, Any]] = None

        # Transit times from distance (one way)
        one_leg_s = int((distance_nm / max(self.cruise_speed_kts, 1.0)) * 3600.0)
        self.outbound_s = max(1, one_leg_s)
        self.inbound_s = max(1, one_leg_s)

        # Derived timeline
        self.ts["launch"] = now
        self.ts["eta_onstation"] = now + self.deck_cycle_s + self.outbound_s
        self.ts["etd_rtb"] = None
        self.ts["eta_recovery"] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "target_cell": self.target_cell,
            "status": self.status,
            "station_radius_nm": self.station_radius_nm,
            "timestamps": self.ts,
            "missiles_left": self.missiles_left,
            "last_engagement": self.last_engagement,
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

        # Sidewinder engagement params (can be overridden by cap_config.json)
        wcfg = (self.cfg.get("weapons") or {}).get("aim9", {})
        self.sw_min_nm = float(wcfg.get("min_nm", 1.0))
        self.sw_max_nm = float(wcfg.get("max_nm", 5.0))
        # Pk control points (nm, probability)
        self.pk_pts: List[Tuple[float, float]] = wcfg.get("pk_points") or [
            (1.0, 0.30), (2.0, 0.55), (2.5, 0.65), (3.0, 0.55), (4.0, 0.35), (5.0, 0.20)
        ]

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
        t = now or time.time()
        if (t - self.last_scramble) < self.min_launch_interval_s:
            return {"ok": False, "message": "Deck cycle in progress"}
        if (t - self.last_scramble) < self.scramble_cooldown_s:
            return {"ok": False, "message": "Scramble cooldown active"}
        if self.ready_pairs < 1:
            return {"ok": False, "message": "No ready pairs on deck"}
        if self.airframe_pool_total < 2:
            return {"ok": False, "message": "Insufficient airframes"}

        m = CAPMission(self._next_id, target_cell, self.cfg, now=t, distance_nm=float(distance_nm))
        self._next_id += 1
        self.missions.append(m)
        self.ready_pairs -= 1
        self.airframe_pool_total -= 2
        self.last_scramble = t
        m.status = "airborne"
        return {"ok": True, "message": f"Hermes: CAP pair launching to {target_cell}", "mission": m.to_dict()}

    def tick(self, now: Optional[float] = None) -> None:
        t = now or time.time()
        for m in self.missions:
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
                    self.ready_pairs = min(self.ready_pairs + 1, self.ready_pairs_max)

        if len(self.missions) > 12:
            self.missions = [m for m in self.missions if m.status != "complete"][-12:]

    # ---------- engagement logic
    def _pk_for_range(self, range_nm: float) -> float:
        return 0.0 if (range_nm < self.sw_min_nm or range_nm > self.sw_max_nm) else float(_interp(range_nm, self.pk_pts))

    def auto_engage(self, distance_nm: Optional[float], locked_target_id: Optional[int], now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        If any mission is on-station and target range is within Sidewinder envelope,
        attempt engagement. If first missile misses and another remains, immediately fire second.
        Returns a summary dict of the last engagement if something happened.
        """
        if distance_nm is None or locked_target_id is None:
            return None
        t = now or time.time()

        # Choose most recent on-station mission with missiles left
        onst = [m for m in self.missions if m.status == "onstation" and m.missiles_left > 0]
        if not onst:
            return None
        m = onst[-1]

        # throttle engagements
        if m.last_engagement_s and (t - m.last_engagement_s) < m.engagement_cooldown_s:
            return None

        if not (self.sw_min_nm <= float(distance_nm) <= self.sw_max_nm):
            return None

        # Fire first missile
        pk = self._pk_for_range(float(distance_nm))
        hit1 = random.random() < pk
        m.missiles_left = max(0, m.missiles_left - 1)

        result = {
            "when": t,
            "target_id": int(locked_target_id),
            "range_nm": float(distance_nm),
            "pk": round(pk, 2),
            "shots": 1,
            "hit": hit1,
        }

        # Fire second if the first missed and we have one left
        if (not hit1) and m.missiles_left > 0:
            hit2 = random.random() < pk  # same pk for simplicity
            m.missiles_left = max(0, m.missiles_left - 1)
            result["shots"] = 2
            result["hit"] = hit2  # overall result: if second hits, we count as hit
            result["second_fired"] = True

        m.last_engagement = result
        m.last_engagement_s = t
        return result

    # ---------- effects surface for Engine (to hook into spawn/defence)
    def current_effects(self) -> Dict[str, Any]:
        eff = self.cfg.get("effects", {}) if self.cfg else {}
        onst = [m for m in self.missions if m.status == "onstation"]
        return {
            "active": len(onst) > 0,
            "stations": [
                {"target_cell": m.target_cell, "radius_nm": m.station_radius_nm, "effects": eff}
                for m in onst
            ]
        }

    # ---------- UI helpers
    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        r = self.readiness(now=now)
        return {"readiness": r, "missions": [m.to_dict() for m in self.missions]}