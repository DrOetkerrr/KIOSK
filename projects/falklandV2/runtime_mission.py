from __future__ import annotations

import time
import random
from typing import Any, Dict, Optional


class MissionCoordinator:
    """Manages mission state snapshots and settings for GameRuntime."""

    def __init__(self, runtime: "GameRuntime") -> None:
        self._runtime = runtime
        self.mission: Optional[Any] = None
        self._settings_cache: Dict[str, Any] = {}

    # ---- lifecycle -----------------------------------------------------
    def attach(self, mission: Optional[Any]) -> None:
        self.mission = mission
        try:
            self._settings_cache = mission.current_settings() if mission is not None else {}
        except Exception:
            self._settings_cache = {}

    # ---- settings helpers ---------------------------------------------
    def settings(self) -> Dict[str, Any]:
        return dict(self._settings_cache)

    def allow_hostile_contacts(self) -> bool:
        try:
            return bool(self._settings_cache.get("hostile_spawns", True))
        except Exception:
            return True

    # ---- context & snapshots ------------------------------------------
    def context(self, now: Optional[float] = None) -> Dict[str, Any]:
        ts = now if now is not None else time.time()
        runtime = self._runtime
        try:
            health = runtime.state_repo.load_health()
        except Exception:
            health = {}
        try:
            state = runtime.engine.public_state()
        except Exception:
            state = {}
        return {"now": ts, "health": health, "state": state}

    def snapshot(self) -> Dict[str, Any]:
        mission = self.mission
        if mission is None:
            return {}
        try:
            ctx = self.context()
            snap = mission.update(ctx, now=ctx["now"])
            self._settings_cache = mission.current_settings()
            self._runtime.cap_orchestrator.sync_wave()
            return snap
        except Exception:
            return {}

    # ---- mission operations -------------------------------------------
    def apply_decision(self, decision_id: str, choice: str) -> Dict[str, Any]:
        mission = self.mission
        if mission is None:
            return {"ok": False, "error": "mission_unavailable"}
        return mission.register_decision(decision_id, choice)

    def activate(self, mission_id: str) -> Dict[str, Any]:
        mission = self.mission
        if mission is None:
            return {"ok": False, "error": "mission_unavailable"}
        previous = dict(self._settings_cache)
        res = mission.activate(mission_id, now=time.time())
        if res.get("ok"):
            try:
                self._settings_cache = mission.current_settings()
            except Exception:
                self._settings_cache = {}
            self._handle_settings_change(previous, self._settings_cache)
        return res

    # ---- internal helpers --------------------------------------------
    def _handle_settings_change(self, previous: Dict[str, Any], new: Dict[str, Any]) -> None:
        runtime = self._runtime
        prev_hostiles = bool(previous.get("hostile_spawns", True))
        new_hostiles = bool(new.get("hostile_spawns", True))
        try:
            if prev_hostiles and not new_hostiles:
                runtime._apply_mission_contact_filters(runtime.radar)
            elif not prev_hostiles and new_hostiles:
                ox, oy = runtime._own_xy()
                bearings = (45.0, 315.0, 90.0)
                for bearing in bearings:
                    try:
                        rng_nm = random.uniform(6.0, 12.0)
                        runtime.radar.force_spawn(ox, oy, "Friendly", bearing, rng_nm)
                    except Exception:
                        continue
        except Exception:
            pass
        runtime._apply_mission_contact_filters(runtime.radar)
        runtime.cap_orchestrator.sync_wave()
