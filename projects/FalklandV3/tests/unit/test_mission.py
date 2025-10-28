import json
from pathlib import Path

from falklandv3.core.mission import MissionLoader, MissionManager


def test_mission_manager_ticks_and_reports_progress(tmp_path: Path):
    data = {
        "id": "timer",
        "label": "Timer Mission",
        "description": "Complete after 60s",
        "duration_s": 60,
        "success": {"all": [{"timer_elapsed": {"seconds": 60}}]},
        "failure": {"any": []},
    }
    missions_dir = tmp_path / "missions"
    missions_dir.mkdir()
    (missions_dir / "timer.json").write_text(json.dumps(data), encoding="utf-8")

    manager = MissionManager(MissionLoader(missions_dir), active_id="timer")
    snap = manager.snapshot()
    assert snap["status"] == "in_progress"
    manager.tick(30.0)
    assert manager.snapshot()["status"] == "in_progress"
    manager.tick(40.0)
    assert manager.snapshot()["status"] == "success"
    assert manager.snapshot()["decision"] is None


def test_mission_success_on_hostile_kill(tmp_path: Path):
    data = {
        "id": "intercept",
        "label": "Intercept",
        "description": "Destroy one hostile",
        "duration_s": 0,
        "success": {"any": [{"hostiles_destroyed_at_least": {"count": 1}}]},
        "failure": {"any": []},
    }
    missions_dir = tmp_path / "missions"
    missions_dir.mkdir()
    (missions_dir / "intercept.json").write_text(json.dumps(data), encoding="utf-8")

    manager = MissionManager(MissionLoader(missions_dir), active_id="intercept")
    assert manager.snapshot()["status"] == "in_progress"
    manager.record_hostile_destroyed()
    assert manager.snapshot()["status"] == "success"
    assert manager.snapshot()["decision"] is None


def test_mission_failure_triggers_decision_and_announce(tmp_path: Path):
    data = {
        "id": "failure_prompt",
        "label": "Failure Prompt",
        "description": "Triggers decision on failure",
        "duration_s": 0,
        "success": {"any": []},
        "failure": {
            "any": [{"timer_elapsed": {"seconds": 0}}],
            "announce": {"voice_fallback": "Mission failure."},
            "decision": {"id": "abandon", "prompt": "Abandon ship?", "timeout_s": 30}
        },
    }
    missions_dir = tmp_path / "missions"
    missions_dir.mkdir()
    (missions_dir / "failure_prompt.json").write_text(json.dumps(data), encoding="utf-8")

    manager = MissionManager(MissionLoader(missions_dir), active_id="failure_prompt")
    snap = manager.snapshot()
    assert snap["status"] == "failure"
    assert snap["decision"] == {
        "id": "abandon",
        "prompt": "Abandon ship?",
        "timeout_s": 30,
        "status": "pending",
    }
    announce = manager.consume_announce()
    assert announce is not None
    assert announce.get("voice_fallback") == "Mission failure."
    assert announce.get("type") == "failure"


def test_mission_failure_on_asset_health(tmp_path: Path):
    data = {
        "id": "protect",
        "label": "Protect",
        "description": "Hold Hermes above 4 lives",
        "duration_s": 0,
        "success": {"any": []},
        "failure": {
            "any": [{"asset_health_at_most": {"asset": "hermes", "lives": 4}}]
        },
    }
    missions_dir = tmp_path / "missions"
    missions_dir.mkdir()
    (missions_dir / "protect.json").write_text(json.dumps(data), encoding="utf-8")

    current = {"hermes": 8}

    def provider(asset: str) -> int | None:
        return current.get(asset)

    manager = MissionManager(MissionLoader(missions_dir), active_id="protect", health_provider=provider)
    assert manager.snapshot()["status"] == "in_progress"
    current["hermes"] = 4
    manager.recompute()
    assert manager.snapshot()["status"] == "failure"
