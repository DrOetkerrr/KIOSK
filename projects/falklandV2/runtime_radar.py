from __future__ import annotations

from typing import Any, Dict, Optional


class RadarRecorder:
    """Adapter that mirrors webdash radar logging while delegating to runtime."""

    def __init__(self, runtime: "GameRuntime") -> None:
        self._runtime = runtime

    def log(self, event: str, data: Dict[str, Any] | None = None) -> None:
        runtime = self._runtime
        payload = data or {}
        try:
            runtime.record_flight({
                "route": f"/radar/{event}",
                "method": "INT",
                "status": 200,
                "duration_ms": 0,
                "request": {},
                "response": {"event": event, **payload},
            })
        except Exception:
            pass

        if event != "ship.alarm.threat_close":
            return

        try:
            cfg = runtime.load_alarm_cfg()
        except Exception:
            cfg = {}
        auto = (cfg.get("auto") or {}).get("threat_close") or {}
        if not bool(auto.get("enabled", False)):
            return

        rng = payload.get("range_nm")
        try:
            threshold = float(auto.get("threshold_nm", 3.0))
        except Exception:
            threshold = 3.0
        try:
            rng_val = float(rng)
        except Exception:
            return
        if rng_val > threshold:
            return
        msg_tpl = str(auto.get("message") or "Combat alarm! Threat inside {range_nm} nm.")
        try:
            msg = msg_tpl.format(range_nm=f"{rng_val:.1f}")
        except Exception:
            msg = msg_tpl
        try:
            runtime.trigger_alarm(
                str(auto.get("sound") or "red-alert.wav"),
                message=msg,
                role=str(auto.get("role") or "Fire Control"),
                loop=False,
            )
        except Exception:
            pass


class RadarBridge:
    """Wires radar callbacks to CAP and runtime helpers."""

    def __init__(self, runtime: "GameRuntime") -> None:
        self._runtime = runtime

    def recorder(self) -> RadarRecorder:
        return RadarRecorder(self._runtime)

    def attach(self, radar: Any, cap: Any) -> None:
        if radar is None:
            return
        # Provide CAP effects snapshot references
        try:
            radar.cap_effects_provider = (lambda: cap.current_effects() if cap is not None else {"active": False})
        except Exception:
            pass
        try:
            radar.cap_missions_provider = (lambda: cap.snapshot().get("missions") if cap is not None else [])
        except Exception:
            pass
        try:
            if cap is not None:
                radius = getattr(cap, "hermes_follow_radius_nm", None)
                if radius is not None:
                    radar.cfg["hermes_follow_radius_nm"] = float(radius)
                period = getattr(cap, "hermes_follow_orbit_period_s", None)
                if period is not None and period > 0:
                    radar.cfg["hermes_follow_orbit_period_s"] = float(period)
        except Exception:
            pass

        if cap is None:
            return

        try:
            cap.bind_target_resolver(lambda cid: self._resolve_contact(radar, cid))
        except Exception:
            pass
        try:
            cap.bind_hit_callback(lambda cid, name, klass, ctx=None: self._runtime.handle_cap_hit(radar, cid, name, klass, ctx))
        except Exception:
            pass

    @staticmethod
    def _resolve_contact(radar: Any, cid: Any) -> Optional[Any]:
        try:
            cid_int = int(cid)
        except Exception:
            return None
        try:
            contacts = list(getattr(radar, "contacts", []) or [])
        except Exception:
            return None
        for contact in contacts:
            try:
                if int(getattr(contact, "id", -1)) == cid_int:
                    return contact
            except Exception:
                continue
        return None
