"""Mission controller for end-state evaluation.

Loads mission definitions from data/missions JSON and evaluates success/failure
conditions against the live game state. Results are emitted via record_event
and optional voice cues so the existing consoles react without direct coupling.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# Type aliases for hooks to avoid import cycles with webdash.
EventHook = Callable[[str, Optional[Dict[str, Any]]], None]
VoiceHook = Callable[..., None]


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception:
        return default


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return fallback


def _safe_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


@dataclass
class MissionOutcome:
    result: str  # "success" | "failure"
    reason: str
    triggered_condition: Optional[str]
    ts: float


@dataclass
class DecisionState:
    decision_id: str
    prompt: str
    timeout_s: float
    started_ts: float
    status: str = "pending"  # pending | acknowledged | timeout
    choice: Optional[str] = None
    resolved_ts: Optional[float] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def time_left(self, now: float) -> Optional[float]:
        if self.status != "pending":
            return None
        deadline = self.started_ts + self.timeout_s
        return max(0.0, deadline - now)


class MissionController:
    """Evaluate mission end conditions using AND/OR logic from JSON."""

    def __init__(
        self,
        data_dir: Path,
        *,
        event_hook: Optional[EventHook] = None,
        voice_hook: Optional[VoiceHook] = None,
        now: Optional[float] = None,
    ) -> None:
        self._data_dir = data_dir
        self._config_path = data_dir / "missions" / "end_conditions.json"
        self._event_hook = event_hook
        self._voice_hook = voice_hook
        self._config = self._load_config()
        self._active_id = self._config.get("active_mission")
        self._mission_def = self._resolve_definition(self._active_id)
        self._start_ts = now if now is not None else time.time()
        self._outcome: Optional[MissionOutcome] = None
        self._last_eval_ts: float = self._start_ts
        self._prev_asset_lives: Dict[str, int] = {}
        self._pending_decision: Optional[DecisionState] = None

    # ---------- public surface ----------
    def update(self, context: Dict[str, Any], *, now: Optional[float] = None) -> Dict[str, Any]:
        """Evaluate mission conditions and return current snapshot."""
        if self._mission_def is None:
            return self.snapshot(now=now)

        ts = now if now is not None else _safe_float(context.get("now"), time.time())
        elapsed = ts - self._start_ts
        assets = self._extract_assets(context.get("health") or {})

        # Ensure previous state defaults to current values for first tick
        if not self._prev_asset_lives:
            self._prev_asset_lives = {k: v.get("lives", 0) for k, v in assets.items()}

        if self._outcome is None:
            failure = self._evaluate_branch(self._mission_def.get("failure"), assets, elapsed, ts)
            if failure is not None:
                self._complete("failure", failure, ts)
            else:
                success = self._evaluate_branch(self._mission_def.get("success"), assets, elapsed, ts)
                if success is not None:
                    self._complete("success", success, ts)

        # Pending decision timeout handling
        if self._pending_decision is not None and self._pending_decision.status == "pending":
            remaining = self._pending_decision.time_left(ts)
            if remaining is not None and remaining <= 0:
                self._pending_decision.status = "timeout"
                self._pending_decision.resolved_ts = ts
                self._emit_event(
                    "mission.decision.timeout",
                    {
                        "id": self._pending_decision.decision_id,
                        "meta": dict(self._pending_decision.meta),
                    },
                )
        self._last_eval_ts = ts
        self._prev_asset_lives = {k: v.get("lives", 0) for k, v in assets.items()}
        return self.snapshot(now=ts, elapsed=elapsed, assets=assets)

    def snapshot(
        self,
        *,
        now: Optional[float] = None,
        elapsed: Optional[float] = None,
        assets: Optional[Dict[str, Dict[str, int]]] = None,
    ) -> Dict[str, Any]:
        ts = now if now is not None else time.time()
        elapsed_val = elapsed if elapsed is not None else ts - self._start_ts
        definition = self._mission_def or {}
        duration = _safe_float(definition.get("duration_s")) if definition else None
        time_left = None
        if duration:
            time_left = max(0.0, duration - elapsed_val)
        snapshot = {
            "id": self._active_id,
            "label": definition.get("label") if definition else None,
            "description": definition.get("description") if definition else None,
            "status": self._outcome.result if self._outcome else ("inactive" if not definition else "in_progress"),
            "elapsed_s": max(0.0, elapsed_val),
            "time_left_s": time_left,
            "outcome": self._outcome.__dict__ if self._outcome else None,
            "pending_decision": self._decision_snapshot(ts),
            "assets": assets if assets is not None else None,
        }
        return snapshot

    def register_decision(self, decision_id: str, choice: str, *, now: Optional[float] = None) -> Dict[str, Any]:
        ts = now if now is not None else time.time()
        if self._pending_decision is None or self._pending_decision.decision_id != str(decision_id):
            return {"ok": False, "error": "unknown_decision"}
        state = self._pending_decision
        if state.status != "pending":
            return {"ok": False, "error": f"decision_{state.status}"}
        state.status = "acknowledged"
        state.choice = str(choice)
        state.resolved_ts = ts
        self._emit_event(
            "mission.decision.choice",
            {
                "id": state.decision_id,
                "choice": state.choice,
                "meta": dict(state.meta),
            },
        )
        return {"ok": True, "decision": self._decision_snapshot(ts)}

    def set_hooks(
        self,
        *,
        event_hook: Optional[EventHook] = None,
        voice_hook: Optional[VoiceHook] = None,
    ) -> None:
        if event_hook is not None:
            self._event_hook = event_hook
        if voice_hook is not None:
            self._voice_hook = voice_hook

    # ---------- internals ----------
    def _decision_snapshot(self, now: float) -> Optional[Dict[str, Any]]:
        if self._pending_decision is None:
            return None
        state = self._pending_decision
        payload = {
            "id": state.decision_id,
            "prompt": state.prompt,
            "status": state.status,
            "timeout_s": state.timeout_s,
            "started_ts": state.started_ts,
            "choice": state.choice,
            "resolved_ts": state.resolved_ts,
            "meta": dict(state.meta),
        }
        time_left = state.time_left(now)
        if time_left is not None:
            payload["time_left_s"] = time_left
        return payload

    def _load_config(self) -> Dict[str, Any]:
        return _load_json(self._config_path, {"missions": {}})

    def _resolve_definition(self, mission_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not mission_id:
            return None
        missions = self._config.get("missions")
        if isinstance(missions, dict):
            mission_def = missions.get(str(mission_id))
            if isinstance(mission_def, dict):
                return mission_def
        return None

    def _extract_assets(self, health: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
        lives = _safe_int(health.get("lives"), 0)
        max_lives = _safe_int(health.get("max_lives"), 0)
        hermes_lives = _safe_int(health.get("hermes_lives"), 0)
        hermes_max = _safe_int(health.get("hermes_max_lives"), 0)
        belgrano_lives = _safe_int(health.get("belgrano_lives"), 0)
        belgrano_max = _safe_int(health.get("belgrano_max_lives"), 0)
        return {
            "sheffield": {"lives": lives, "max_lives": max_lives},
            "hermes": {"lives": hermes_lives, "max_lives": hermes_max},
            "belgrano": {"lives": belgrano_lives, "max_lives": belgrano_max},
        }

    def _evaluate_branch(
        self,
        branch: Any,
        assets: Dict[str, Dict[str, int]],
        elapsed_s: float,
        now: float,
    ) -> Optional[str]:
        if not branch:
            return None
        result, reason = self._eval_node(branch, assets, elapsed_s, now)
        return reason if result else None

    def _eval_node(
        self,
        node: Any,
        assets: Dict[str, Dict[str, int]],
        elapsed_s: float,
        now: float,
    ) -> Tuple[bool, Optional[str]]:
        if not isinstance(node, dict):
            return False, None
        if "all" in node:
            reasons: List[str] = []
            for child in node.get("all", []):
                ok, reason = self._eval_node(child, assets, elapsed_s, now)
                if not ok:
                    return False, None
                if reason:
                    reasons.append(reason)
            return True, "; ".join(reasons) if reasons else "all"
        if "any" in node:
            for child in node.get("any", []):
                ok, reason = self._eval_node(child, assets, elapsed_s, now)
                if ok:
                    return True, reason or "any"
            return False, None

        # Leaf predicates
        if "timer_elapsed" in node:
            seconds = _safe_float((node["timer_elapsed"] or {}).get("seconds"))
            if seconds <= 0:
                return False, None
            if elapsed_s >= seconds:
                return True, f"timer_elapsed:{int(seconds)}"
            return False, None

        if "asset_health_at_least" in node:
            spec = node["asset_health_at_least"] or {}
            asset = str(spec.get("asset") or "").lower()
            target = _safe_int(spec.get("lives"))
            if asset in assets and assets[asset]["lives"] >= target:
                return True, f"{asset}_lives>={target}"
            return False, None

        if "asset_health_at_most" in node:
            spec = node["asset_health_at_most"] or {}
            asset = str(spec.get("asset") or "").lower()
            target = _safe_int(spec.get("lives"))
            if asset in assets and assets[asset]["lives"] <= target:
                return True, f"{asset}_lives<={target}"
            return False, None

        if "asset_destroyed" in node:
            asset = str(node.get("asset_destroyed") or "").lower()
            if asset in assets and assets[asset]["lives"] <= 0:
                return True, f"{asset}_destroyed"
            return False, None

        if "drop_below" in node:
            spec = node["drop_below"] or {}
            asset = str(spec.get("asset") or "").lower()
            from_val = _safe_int(spec.get("from"))
            to_val = _safe_int(spec.get("to"))
            if asset not in assets:
                return False, None
            prev = self._prev_asset_lives.get(asset, assets[asset]["lives"])
            cur = assets[asset]["lives"]
            if prev >= from_val and cur <= to_val:
                return True, f"{asset}_drop_{prev}->{cur}"
            return False, None

        if "timer_no_longer_than" in node:
            # Timer must still be within limit (used for failure conditions on delay)
            spec = node["timer_no_longer_than"] or {}
            seconds = _safe_float(spec.get("seconds"))
            if seconds <= 0:
                return False, None
            if elapsed_s <= seconds:
                return True, f"timer<={int(seconds)}"
            return False, None

        if "elapsed_between" in node:
            spec = node["elapsed_between"] or {}
            lo = _safe_float(spec.get("min_seconds"), 0.0)
            hi = _safe_float(spec.get("max_seconds"), float("inf"))
            if lo <= elapsed_s <= hi:
                return True, f"timer_between:{int(lo)}-{int(hi)}"
            return False, None

        if "time_since_outcome" in node and self._outcome is not None:
            spec = node["time_since_outcome"] or {}
            min_seconds = _safe_float(spec.get("min_seconds"))
            if (now - self._outcome.ts) >= min_seconds:
                return True, f"after_outcome>={int(min_seconds)}"
            return False, None

        return False, None

    def _complete(self, result: str, reason: str, ts: float) -> None:
        announce = self._mission_def.get(result, {}).get("announce", {}) if self._mission_def else {}
        self._outcome = MissionOutcome(result=result, reason=reason, triggered_condition=reason, ts=ts)
        payload = {
            "mission_id": self._active_id,
            "result": result,
            "reason": reason,
        }
        self._emit_event(f"mission.{result}", payload)
        self._speak(announce, payload)
        decision_cfg = (self._mission_def or {}).get(result, {}).get("decision") if result == "failure" else None
        if isinstance(decision_cfg, dict):
            self._start_decision(decision_cfg, ts)

    def _start_decision(self, config: Dict[str, Any], ts: float) -> None:
        decision_id = str(config.get("id") or "")
        prompt = str(config.get("prompt") or "Decision required")
        timeout_s = _safe_float(config.get("timeout_s"), 60.0)
        self._pending_decision = DecisionState(
            decision_id=decision_id or "decision",
            prompt=prompt,
            timeout_s=timeout_s,
            started_ts=ts,
            meta={k: v for k, v in config.items() if k not in {"id", "prompt", "timeout_s"}},
        )
        announce = config.get("announce") or {}
        payload = {
            "mission_id": self._active_id,
            "decision_id": self._pending_decision.decision_id,
            "prompt": prompt,
            "timeout_s": timeout_s,
        }
        self._emit_event("mission.decision.prompt", payload)
        self._speak(announce, payload)

    def _emit_event(self, event_id: str, data: Dict[str, Any] | None) -> None:
        if callable(self._event_hook):
            try:
                self._event_hook(event_id, data or {})
            except Exception:
                pass

    def _speak(self, announce_cfg: Dict[str, Any], ctx: Dict[str, Any]) -> None:
        if not callable(self._voice_hook):
            return
        event_id = announce_cfg.get("voice_event")
        fallback = announce_cfg.get("voice_fallback")
        role = announce_cfg.get("voice_role")
        if not (event_id or fallback):
            return
        try:
            if event_id:
                self._voice_hook(str(event_id), ctx, fallback=fallback, role=role)
            else:
                self._voice_hook("mission.announce", ctx, fallback=fallback, role=role)
        except Exception:
            pass
