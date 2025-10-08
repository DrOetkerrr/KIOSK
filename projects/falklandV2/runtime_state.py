from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


class StateRepository:
    """Lightweight accessor for runtime data/state paths and JSON helpers."""

    def __init__(self, core_module) -> None:
        self._core = core_module

    # ---- Path accessors -------------------------------------------------
    @property
    def log_dir(self) -> Path:
        return Path(self._core.LOG_DIR)

    @property
    def flight_path(self) -> Path:
        return Path(self._core.FLIGHT_PATH)

    @property
    def flight_max_bytes(self) -> int:
        return int(self._core.FLIGHT_MAX_BYTES)

    @property
    def data_dir(self) -> Path:
        return Path(self._core.DATA_DIR)

    @property
    def state_dir(self) -> Path:
        return Path(self._core.STATE_DIR)

    @property
    def ammo_path(self) -> Path:
        return Path(self._core.AMMO_PATH)

    @property
    def arming_path(self) -> Path:
        return Path(self._core.ARMING_PATH)

    @property
    def weapons_catalog_path(self) -> Path:
        return Path(self._core.WEAP_CATALOG_PATH)

    @property
    def contacts_path(self) -> Path:
        return Path(self._core.CONTACTS_PATH)

    @property
    def crew_path(self) -> Path:
        return Path(self._core.CREW_PATH)

    @property
    def alarm_cfg_path(self) -> Path:
        return Path(self._core.ALARM_CFG_PATH)

    @property
    def health_path(self) -> Path:
        return Path(self._core.HEALTH_PATH)

    @property
    def tts_dir(self) -> Path:
        return Path(self._core.TTS_DIR)

    @property
    def voice_events_path(self) -> Path:
        return Path(self._core.VOICE_EVENTS_PATH)

    @property
    def skirmishes_path(self) -> Path:
        return Path(self._core.SKIRMISHES_PATH)

    @property
    def roadmap_path(self) -> Path:
        return Path(self._core.ROADMAP_PATH)

    @property
    def voices_dir(self) -> Path:
        return Path(self._core.VOICES_DIR)

    # ---- JSON helpers ---------------------------------------------------
    @staticmethod
    def load_json(path: Path, default: Optional[Any] = None) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except Exception:
            return default

    @staticmethod
    def save_json(path: Path, payload: Any) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass

    # ---- domain helpers -------------------------------------------------
    def load_ammo(self) -> Dict[str, Any]:
        data = self.load_json(self.ammo_path, {})
        return data if isinstance(data, dict) else {}

    def save_ammo(self, obj: Dict[str, Any]) -> None:
        self.save_json(self.ammo_path, obj)

    def load_arming(self) -> Dict[str, Any]:
        data = self.load_json(self.arming_path, {})
        return data if isinstance(data, dict) else {}

    def save_arming(self, obj: Dict[str, Any]) -> None:
        self.save_json(self.arming_path, obj)

    def load_health(self) -> Dict[str, Any]:
        data = self.load_json(self.health_path, {})
        return data if isinstance(data, dict) else {}
