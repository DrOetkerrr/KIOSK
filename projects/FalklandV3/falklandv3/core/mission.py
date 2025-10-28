"""Mission configuration loader and simple timer-based evaluator."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional


@dataclass(frozen=True)
class MissionCondition:
    kind: str
    params: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class MissionConditionGroup:
    mode: str  # "all" or "any"
    conditions: List[MissionCondition]


@dataclass(frozen=True)
class MissionConfig:
    mission_id: str
    label: str
    description: str
    duration_s: float
    success: MissionConditionGroup
    failure: MissionConditionGroup
    failure_decision: Optional[Dict[str, object]]
    failure_announce: Optional[Dict[str, object]]
    success_announce: Optional[Dict[str, object]]


@dataclass(frozen=True)
class MissionState:
    status: str  # inactive | in_progress | success | failure
    elapsed_s: float
    time_left_s: Optional[float]
    decision: Optional[Dict[str, object]]


class MissionLoader:
    """Loads mission definitions from JSON files."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self, mission_id: str) -> MissionConfig:
        path = self.root / f"{mission_id}.json"
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        duration = float(data.get("duration_s", 0.0))
        success_payload = data.get("success")
        failure_payload = data.get("failure")
        success = self._parse_group(success_payload, default_timer=duration)
        failure = self._parse_group(failure_payload)
        failure_decision = None
        failure_announce = None
        success_announce = None
        if isinstance(failure_payload, dict):
            failure_decision = failure_payload.get("decision")
            failure_announce = failure_payload.get("announce")
        if isinstance(success_payload, dict):
            success_announce = success_payload.get("announce")
        return MissionConfig(
            mission_id=mission_id,
            label=str(data.get("label", mission_id)),
            description=str(data.get("description", "")),
            duration_s=duration,
            success=success,
            failure=failure,
            failure_decision=failure_decision,
            failure_announce=failure_announce,
            success_announce=success_announce,
        )

    def _parse_group(self, payload: Optional[dict], *, default_timer: float | None = None) -> MissionConditionGroup:
        if not isinstance(payload, dict):
            if default_timer and default_timer > 0:
                return MissionConditionGroup(
                    mode="all",
                    conditions=[MissionCondition(kind="timer_elapsed", value=float(default_timer))],
                )
            return MissionConditionGroup(mode="any", conditions=[])
        if "all" in payload:
            mode = "all"
            entries = payload.get("all", [])
        else:
            mode = "any"
            entries = payload.get("any", [])
        conditions: List[MissionCondition] = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            if "timer_elapsed" in entry:
                data = entry["timer_elapsed"]
                seconds = float(data.get("seconds", 0.0)) if isinstance(data, dict) else float(data or 0.0)
                conditions.append(MissionCondition(kind="timer_elapsed", params={"seconds": seconds}))
            elif "hostiles_destroyed_at_least" in entry:
                data = entry["hostiles_destroyed_at_least"]
                count = float(data.get("count", 0.0)) if isinstance(data, dict) else float(data or 0.0)
                conditions.append(MissionCondition(kind="hostiles_destroyed_at_least", params={"count": count}))
            elif "asset_health_at_most" in entry:
                data = entry["asset_health_at_most"]
                if isinstance(data, dict):
                    asset = str(data.get("asset", ""))
                    lives = float(data.get("lives", 0.0))
                else:
                    asset = str(data)
                    lives = 0.0
                conditions.append(
                    MissionCondition(kind="asset_health_at_most", params={"asset": asset, "lives": lives})
                )
            elif "asset_health_at_least" in entry:
                data = entry["asset_health_at_least"]
                if isinstance(data, dict):
                    asset = str(data.get("asset", ""))
                    lives = float(data.get("lives", 0.0))
                else:
                    asset = str(data)
                    lives = 0.0
                conditions.append(
                    MissionCondition(kind="asset_health_at_least", params={"asset": asset, "lives": lives})
                )
            elif "asset_destroyed" in entry:
                data = entry["asset_destroyed"]
                asset = str(data.get("asset", "")) if isinstance(data, dict) else str(data)
                conditions.append(MissionCondition(kind="asset_destroyed", params={"asset": asset}))
            elif "drop_below" in entry:
                data = entry["drop_below"]
                if isinstance(data, dict):
                    asset = str(data.get("asset", ""))
                    from_val = float(data.get("from", 0.0))
                    to_val = float(data.get("to", 0.0))
                else:
                    asset = str(data)
                    from_val = 0.0
                    to_val = 0.0
                conditions.append(
                    MissionCondition(
                        kind="drop_below",
                        params={"asset": asset, "from": from_val, "to": to_val},
                    )
                )
        if not conditions and default_timer and default_timer > 0 and mode == "all":
            conditions.append(MissionCondition(kind="timer_elapsed", params={"seconds": float(default_timer)}))
        return MissionConditionGroup(mode=mode, conditions=conditions)


class MissionManager:
    """Tracks current mission and computes state."""

    def __init__(
        self,
        loader: MissionLoader,
        *,
        active_id: str = "example",
        health_provider: Optional[Callable[[str], Optional[int]]] = None,
    ) -> None:
        self.loader = loader
        self.active_id = active_id
        self._config = self.loader.load(active_id)
        self._elapsed_s = 0.0
        self._hostiles_destroyed = 0
        self._status = "inactive"
        self._active_decision: Optional[Dict[str, object]] = None
        self._pending_announce: Optional[Dict[str, object]] = None
        self._decision_status: Optional[str] = None
        self._decision_choice: Optional[str] = None
        self._health_provider = health_provider
        self._initial_health: Dict[str, Optional[int]] = {}
        if self._health_provider is not None:
            self._capture_initial_health()
        self._set_status(self._evaluate_status())

    def tick(self, dt_seconds: float) -> None:
        if self._status in {"success", "failure"}:
            return
        self._elapsed_s = max(0.0, self._elapsed_s + max(0.0, dt_seconds))
        self._set_status(self._evaluate_status())

    def record_hostile_destroyed(self) -> None:
        if self._status in {"success", "failure"}:
            return
        self._hostiles_destroyed += 1
        self._set_status(self._evaluate_status())

    def recompute(self) -> None:
        self._set_status(self._evaluate_status())

    def resolve_decision(self, decision_id: str, choice: str) -> Optional[Dict[str, object]]:
        if not self._active_decision or (self._decision_status and self._decision_status != "pending"):
            raise ValueError("No mission decision pending")
        expected = str(self._active_decision.get("id", "")) if isinstance(self._active_decision, dict) else ""
        if expected and decision_id and decision_id != expected:
            raise ValueError("Decision id mismatch")
        self._decision_status = "resolved"
        self._decision_choice = choice
        announce_cfg = (
            self._active_decision.get("announce")
            if isinstance(self._active_decision, dict)
            else None
        )
        if isinstance(announce_cfg, dict):
            announce = dict(announce_cfg)
            announce.setdefault("type", "decision")
            announce.setdefault("choice", choice)
            return announce
        return None

    def recompute(self) -> None:
        self._set_status(self._evaluate_status())

    def snapshot(self) -> Dict[str, object]:
        state = MissionState(
            status=self._status,
            elapsed_s=self._elapsed_s,
            time_left_s=self._time_left(),
            decision=self._decision_snapshot(),
        )
        return {
            "id": self._config.mission_id,
            "label": self._config.label,
            "description": self._config.description,
            "status": state.status,
            "elapsed_s": state.elapsed_s,
            "time_left_s": state.time_left_s,
            "decision": state.decision,
        }

    def consume_announce(self) -> Optional[Dict[str, object]]:
        announce = self._pending_announce
        self._pending_announce = None
        return announce

    def set_health_provider(self, provider: Callable[[str], Optional[int]]) -> None:
        self._health_provider = provider
        self._capture_initial_health()

    def _decision_snapshot(self) -> Optional[Dict[str, object]]:
        if not self._active_decision:
            return None
        snap = dict(self._active_decision)
        snap["status"] = self._decision_status or "pending"
        if self._decision_choice is not None:
            snap["choice"] = self._decision_choice
        return snap

    # ----- internals -------------------------------------------------
    def _time_left(self) -> Optional[float]:
        if self._config.duration_s <= 0:
            return None
        return max(0.0, self._config.duration_s - self._elapsed_s)

    def _evaluate_status(self) -> str:
        if self._check_group(self._config.failure):
            return "failure"
        if self._check_group(self._config.success):
            return "success"
        return "in_progress"

    def _check_group(self, group: MissionConditionGroup) -> bool:
        if not group.conditions:
            return False
        evaluator = all if group.mode == "all" else any
        results = [self._check_condition(cond) for cond in group.conditions]
        return evaluator(results)

    def _check_condition(self, condition: MissionCondition) -> bool:
        if condition.kind == "timer_elapsed":
            seconds = float(condition.params.get("seconds", 0.0))
            return self._elapsed_s >= seconds
        if condition.kind == "hostiles_destroyed_at_least":
            count = float(condition.params.get("count", 0.0))
            return self._hostiles_destroyed >= count
        if condition.kind in {"asset_health_at_most", "asset_health_at_least", "asset_destroyed", "drop_below"}:
            asset = str(condition.params.get("asset", ""))
            if not asset:
                return False
            lives = self._get_asset_lives(asset)
            if lives is None:
                return False
            if condition.kind == "asset_health_at_most":
                threshold = float(condition.params.get("lives", 0.0))
                return lives <= threshold
            if condition.kind == "asset_health_at_least":
                threshold = float(condition.params.get("lives", 0.0))
                return lives >= threshold
            if condition.kind == "asset_destroyed":
                return lives <= 0
            if condition.kind == "drop_below":
                from_val = float(condition.params.get("from", 0.0))
                to_val = float(condition.params.get("to", 0.0))
                initial = self._initial_health.get(asset)
                if initial is None:
                    initial = self._get_asset_lives(asset)
                return initial is not None and initial >= from_val and lives <= to_val
        return False

    def _set_status(self, new_status: str) -> None:
        if new_status == self._status:
            return
        self._status = new_status
        if new_status == "failure":
            self._active_decision = self._clone_or_none(self._config.failure_decision)
            self._decision_status = "pending" if self._active_decision else None
            self._decision_choice = None
            self._pending_announce = self._build_announce_payload("failure", self._config.failure_announce)
        elif new_status == "success":
            self._active_decision = None
            self._decision_status = None
            self._decision_choice = None
            self._pending_announce = self._build_announce_payload("success", self._config.success_announce)
        else:
            self._active_decision = None
            self._decision_status = None
            self._decision_choice = None

    def _clone_or_none(self, payload: Optional[Dict[str, object]]) -> Optional[Dict[str, object]]:
        if payload is None:
            return None
        return dict(payload)

    def _build_announce_payload(self, kind: str, payload: Optional[Dict[str, object]]) -> Optional[Dict[str, object]]:
        if not payload:
            return None
        result = dict(payload)
        result.setdefault("type", kind)
        return result

    def _get_asset_lives(self, asset: str) -> Optional[int]:
        if self._health_provider is None:
            return None
        return self._health_provider(asset)

    def _capture_initial_health(self) -> None:
        if self._health_provider is None:
            return
        assets = set()
        for group in (self._config.success, self._config.failure):
            for cond in group.conditions:
                asset = cond.params.get("asset")
                if asset:
                    assets.add(str(asset))
        for asset in assets:
            self._initial_health[asset] = self._health_provider(asset)
