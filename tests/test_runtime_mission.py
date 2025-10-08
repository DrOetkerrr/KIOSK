from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projects.falklandV2.runtime_mission import MissionCoordinator


class DummyStateRepo:
    def load_health(self):
        return {"ship": {"hp": 1}}


class DummyEngine:
    def public_state(self):
        return {"ship": {"pos": "K13"}}


class DummyRadar:
    def __init__(self):
        self.contacts = []
        self.spawn_calls = []

    def force_spawn(self, ox, oy, allegiance, bearing, rng_nm):
        self.spawn_calls.append((ox, oy, allegiance, bearing, rng_nm))


class DummyCapOrchestrator:
    def __init__(self):
        self.synced = 0

    def sync_wave(self):
        self.synced += 1


class DummyRuntime:
    def __init__(self):
        self.state_repo = DummyStateRepo()
        self.engine = DummyEngine()
        self.radar = DummyRadar()
        self.cap_orchestrator = DummyCapOrchestrator()
        self._filter_calls = 0

    def _apply_mission_contact_filters(self, radar):
        self._filter_calls += 1

    def _own_xy(self):
        return (20.0, 20.0)


class DummyMission:
    def __init__(self):
        self.settings = {"hostile_spawns": True}
        self.update_calls = 0

    def current_settings(self):
        return dict(self.settings)

    def update(self, ctx, now):
        self.update_calls += 1
        return {"ctx": ctx}

    def register_decision(self, decision_id, choice):
        return {"ok": True, "decision": decision_id, "choice": choice}

    def activate(self, mission_id, now):
        self.settings["hostile_spawns"] = False
        return {"ok": True, "id": mission_id}


def test_mission_context_uses_state_repo():
    runtime = DummyRuntime()
    coord = MissionCoordinator(runtime)
    ctx = coord.context()
    assert ctx["health"] == {"ship": {"hp": 1}}
    assert ctx["state"]["ship"]["pos"] == "K13"


def test_mission_snapshot_updates_settings_and_syncs_wave():
    runtime = DummyRuntime()
    coord = MissionCoordinator(runtime)
    mission = DummyMission()
    coord.attach(mission)
    snap = coord.snapshot()
    assert snap
    assert mission.update_calls == 1
    assert runtime.cap_orchestrator.synced >= 1


def test_activate_handles_settings_change():
    runtime = DummyRuntime()
    coord = MissionCoordinator(runtime)
    mission = DummyMission()
    coord.attach(mission)
    result = coord.activate("test-mission")
    assert result["ok"] is True
    assert coord.allow_hostile_contacts() is False
    assert runtime._filter_calls >= 1
