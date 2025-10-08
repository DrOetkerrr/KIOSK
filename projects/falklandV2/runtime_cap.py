from __future__ import annotations

import sys
import time
from typing import Any, Dict, Optional


class CAPOrchestrator:
    """Encapsulates CAP-specific runtime wiring."""

    def __init__(self, runtime: "GameRuntime") -> None:
        self._runtime = runtime

    # ---- attach/detach helpers -------------------------------------------------
    def attach(self, cap: Any) -> None:
        if cap is None:
            return
        try:
            cap.bind_hit_callback(lambda cid, name, klass, ctx=None: self.handle_hit(cid, name, klass, ctx))
        except Exception:
            pass

    # ---- wave handling ---------------------------------------------------------
    def derive_wave(self, settings: Dict[str, Any]) -> Optional[str]:
        wave: Optional[str] = None
        if isinstance(settings, dict):
            for key in ("wave_id", "wave", "phase_id", "phase", "stage", "segment"):
                val = settings.get(key)
                if isinstance(val, str):
                    candidate = val.strip()
                    if candidate:
                        wave = candidate
                        break
        if not wave:
            active = getattr(self._runtime.mission, "_active_id", None)
            if isinstance(active, str):
                candidate = active.strip()
                if candidate:
                    wave = candidate
        return wave

    def sync_wave(self) -> None:
        cap = getattr(self._runtime, "cap", None)
        if cap is None or not hasattr(cap, "set_wave_context"):
            return
        try:
            wave = self.derive_wave(self._runtime.mission_coordinator.settings())
        except Exception:
            wave = None
        try:
            cap.set_wave_context(wave)
        except Exception:
            pass

    # ---- hit processing --------------------------------------------------------
    def handle_hit(self, cid: int, name: str, klass: str, ctx: Optional[Dict[str, Any]] = None) -> None:
        radar = getattr(self._runtime, "radar", None)
        try:
            nm = str(name or "")
            kl = str(klass or "")
        except Exception:
            nm = str(name)
            kl = str(klass)
        handled = False
        sunk = False
        wd = None
        try:
            wd = sys.modules.get("projects.falklandV2.webdash")  # type: ignore
            if wd is None:
                from projects.falklandV2 import webdash as wd  # type: ignore
        except Exception:
            wd = None  # type: ignore
        if wd is not None:
            loadout = ""
            weapon_label = None
            if isinstance(ctx, dict):
                try:
                    loadout = str(ctx.get("loadout", "")).lower()
                except Exception:
                    loadout = ""
                weapon_label = ctx.get("weapon")
            if loadout == "bombs":
                weapon = str(weapon_label or "Bomb")
                with wd.STATE_LOCK:
                    try:
                        handled, sunk = self._runtime.core.apply_enemy_ship_damage(
                            wd, cid, weapon=weapon, target_name=nm, target_class=kl
                        )
                    except Exception:
                        handled = False
        if handled:
            return
        try:
            contact_list = list(getattr(radar, "contacts", []) or []) if radar is not None else []
            filtered = [c for c in contact_list if int(getattr(c, "id", -1)) != int(cid)]
            if radar is not None:
                radar.contacts = filtered
        except Exception:
            pass
