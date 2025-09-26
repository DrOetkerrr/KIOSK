from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from projects.falklandV2.subsystems.mission import MissionController


def _write_config(tmp_path: Path, data: dict) -> Path:
    missions_dir = tmp_path / "missions"
    missions_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = missions_dir / "end_conditions.json"
    cfg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return cfg_path


def test_mission_success_on_timer(tmp_path: Path) -> None:
    cfg = {
        "active_mission": "timed",
        "missions": {
            "timed": {
                "label": "Timed Mission",
                "duration_s": 10,
                "success": {"all": [{"timer_elapsed": {"seconds": 5}}]},
            }
        },
    }
    _write_config(tmp_path, cfg)
    controller = MissionController(tmp_path, now=0.0)
    ctx = {"now": 6.0, "health": {"hermes_lives": 8, "hermes_max_lives": 8, "lives": 4, "max_lives": 4}}
    snap = controller.update(ctx, now=ctx["now"])
    assert snap["status"] == "success"
    assert snap["outcome"]["reason"] == "timer_elapsed:5"


def test_mission_failure_on_drop_with_decision(tmp_path: Path) -> None:
    events: list[tuple[str, dict]] = []
    voices: list[tuple[str, dict]] = []

    def _event_hook(event_id: str, data: dict | None) -> None:
        events.append((event_id, dict(data or {})))

    def _voice_hook(event_id: str, ctx: dict | None, **kwargs) -> None:
        voices.append((event_id, dict(ctx or {})))

    cfg = {
        "active_mission": "protect",
        "missions": {
            "protect": {
                "label": "Protect Hermes",
                "failure": {
                    "any": [
                        {"drop_below": {"asset": "hermes", "from": 8, "to": 4}}
                    ],
                    "announce": {"voice_event": "mission.failure"},
                    "decision": {
                        "id": "abandon_ship",
                        "prompt": "Abandon ship?",
                        "timeout_s": 30,
                        "announce": {"voice_event": "mission.decision.prompt"}
                    },
                },
            }
        },
    }
    _write_config(tmp_path, cfg)
    controller = MissionController(tmp_path, now=0.0, event_hook=_event_hook, voice_hook=_voice_hook)

    # Initial healthy tick
    ctx1 = {"now": 1.0, "health": {"hermes_lives": 8, "hermes_max_lives": 8, "lives": 4, "max_lives": 4}}
    controller.update(ctx1, now=ctx1["now"])
    assert controller.snapshot()["status"] == "in_progress"

    # Drop below threshold
    ctx2 = {"now": 2.0, "health": {"hermes_lives": 3, "hermes_max_lives": 8, "lives": 2, "max_lives": 4}}
    snap = controller.update(ctx2, now=ctx2["now"])
    assert snap["status"] == "failure"
    assert snap["pending_decision"]["id"] == "abandon_ship"
    assert any(evt[0] == "mission.failure" for evt in events)
    assert any(v[0] == "mission.failure" for v in voices)
    assert any(v[0] == "mission.decision.prompt" for v in voices)

    # Register decision choice
    res = controller.register_decision("abandon_ship", "abandon", now=10.0)
    assert res["ok"] is True
    assert res["decision"]["choice"] == "abandon"
    assert any(evt[0] == "mission.decision.choice" for evt in events)


def test_mission_decision_timeout(tmp_path: Path) -> None:
    events: list[tuple[str, dict]] = []

    def _event_hook(event_id: str, data: dict | None) -> None:
        events.append((event_id, dict(data or {})))

    cfg = {
        "active_mission": "fail_fast",
        "missions": {
            "fail_fast": {
                "label": "Fail Fast",
                "failure": {
                    "any": [
                        {"asset_health_at_most": {"asset": "sheffield", "lives": 0}}
                    ],
                    "decision": {
                        "id": "abandon_ship",
                        "prompt": "Abandon ship?",
                        "timeout_s": 5
                    }
                }
            }
        }
    }
    _write_config(tmp_path, cfg)
    controller = MissionController(tmp_path, now=0.0, event_hook=_event_hook)

    ctx1 = {"now": 1.0, "health": {"hermes_lives": 8, "hermes_max_lives": 8, "lives": 0, "max_lives": 4}}
    controller.update(ctx1, now=ctx1["now"])
    # Wait beyond timeout
    ctx2 = {"now": 7.0, "health": {"hermes_lives": 8, "hermes_max_lives": 8, "lives": 0, "max_lives": 4}}
    snap = controller.update(ctx2, now=ctx2["now"])
    decision = snap["pending_decision"]
    assert decision["status"] == "timeout"
    assert any(evt[0] == "mission.decision.timeout" for evt in events)

