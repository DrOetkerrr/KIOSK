from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projects.falklandV2.runtime_radar import RadarBridge


class DummyRuntime:
    def __init__(self) -> None:
        self.flights = []
        self.alarms = []
        self.cap_hits = []

    def record_flight(self, entry):
        self.flights.append(entry)

    def load_alarm_cfg(self):
        return {
            "auto": {
                "threat_close": {
                    "enabled": True,
                    "threshold_nm": 5,
                    "message": "Alert at {range_nm} nm",
                    "sound": "alarm.wav",
                    "role": "Radar",
                }
            }
        }

    def trigger_alarm(self, sound, *, message, role, loop):
        self.alarms.append((sound, message, role, loop))

    def handle_cap_hit(self, radar, cid, name, klass, ctx=None):
        self.cap_hits.append((cid, name, klass, ctx))


def test_radar_recorder_logs_and_triggers_alarm():
    runtime = DummyRuntime()
    bridge = RadarBridge(runtime)
    recorder = bridge.recorder()

    recorder.log("scan", {"count": 1})
    assert runtime.flights[-1]["response"]["event"] == "scan"

    recorder.log("ship.alarm.threat_close", {"range_nm": 2.5})
    assert runtime.alarms[-1][0] == "alarm.wav"
    assert "2.5" in runtime.alarms[-1][1]


def test_radar_bridge_attaches_cap_hooks():
    runtime = DummyRuntime()
    bridge = RadarBridge(runtime)

    contacts = [SimpleNamespace(id=7, name="Target"), SimpleNamespace(id=8, name="Other")]
    radar = SimpleNamespace(contacts=contacts)

    class CapStub:
        def __init__(self):
            self.resolver = None
            self.hit = None

        def current_effects(self):
            return {"active": True}

        def snapshot(self):
            return {"missions": []}

        def bind_target_resolver(self, fn):
            self.resolver = fn

        def bind_hit_callback(self, fn):
            self.hit = fn

    cap = CapStub()

    bridge.attach(radar, cap)

    assert callable(cap.resolver)
    assert cap.resolver(7) is contacts[0]
    assert cap.resolver(99) is None

    assert callable(cap.hit)
    cap.hit(7, "Target", "Ship", {"loadout": "bombs"})
    assert runtime.cap_hits[-1][0] == 7
