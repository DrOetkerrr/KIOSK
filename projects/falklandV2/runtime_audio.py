from __future__ import annotations

import time
from typing import Any, Dict


class AudioCoordinator:
    """Encapsulates manipulation of the shared AUDIO_STATE structure."""

    BASE_STATE = {
        "last_launch": None,
        "last_result": None,
        "radio": None,
        "alarm": None,
        "cap_launch": None,
        "cap_recovery": None,
        "enemy_bomb": None,
        "shots_in_flight": [],
        "intro": None,
    }

    def __init__(self, core_module) -> None:
        self._core = core_module

    @property
    def state(self) -> Dict[str, Any]:
        return self._core.AUDIO_STATE

    def ensure_intro(self) -> None:
        try:
            self.state["intro"] = self._core.build_intro_payload()
        except Exception:
            self.state["intro"] = None

    def reset(self) -> None:
        state = self.state
        state.clear()
        state.update(self.BASE_STATE)
        self.ensure_intro()

    def record_launch(self, weapon_name: str) -> None:
        try:
            key = self._core._sound_key_for_weapon(weapon_name)
        except Exception:
            key = weapon_name
        try:
            self.state["last_launch"] = {"weapon": key, "ts": time.time()}
        except Exception:
            pass
