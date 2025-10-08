from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projects.falklandV2.runtime_cap import CAPOrchestrator


class DummyRuntime:
    def __init__(self):
        self.core = SimpleNamespace(apply_enemy_ship_damage=lambda *args, **kwargs: (False, False))
        self.radar = SimpleNamespace(contacts=[SimpleNamespace(id=5), SimpleNamespace(id=6)])
        self.cap = None
        self.mission = SimpleNamespace(_active_id="alpha")
        self.mission_coordinator = SimpleNamespace(
            settings=lambda: {"wave_id": "alpha"},
            allow_hostile_contacts=lambda: True,
        )


def test_cap_sync_wave_sets_context():
    runtime = DummyRuntime()

    class CapStub:
        def __init__(self):
            self.wave_set = None

        def set_wave_context(self, wave):
            self.wave_set = wave

    cap = CapStub()
    orchestrator = CAPOrchestrator(runtime)
    orchestrator.attach(cap)
    runtime.cap = cap
    orchestrator.sync_wave()
    assert cap.wave_set == "alpha"


def test_cap_handle_hit_removes_contact_when_unhandled():
    runtime = DummyRuntime()
    orchestrator = CAPOrchestrator(runtime)

    sys.modules.pop("projects.falklandV2.webdash", None)

    orchestrator.handle_hit(5, "Target", "Ship", {})
    ids = [c.id for c in runtime.radar.contacts]
    assert ids == [6]
