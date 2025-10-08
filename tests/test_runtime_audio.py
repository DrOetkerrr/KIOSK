from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projects.falklandV2.runtime_audio import AudioCoordinator


class DummyCore:
    def __init__(self, intro: str = "intro-payload") -> None:
        self.AUDIO_STATE = {}
        self._payload = intro

    def build_intro_payload(self):
        if self._payload == "raise":
            raise RuntimeError("boom")
        return self._payload

    @staticmethod
    def _sound_key_for_weapon(name: str) -> str:
        return name.lower().replace(" ", "-")


def test_audio_reset_sets_base_state():
    core = DummyCore()
    audio = AudioCoordinator(core)
    audio.reset()
    assert audio.state["intro"] == "intro-payload"
    assert audio.state["last_launch"] is None


def test_audio_ensure_intro_handles_exception():
    core = DummyCore("raise")
    audio = AudioCoordinator(core)
    audio.ensure_intro()
    assert audio.state["intro"] is None


def test_audio_record_launch_uses_sound_key():
    core = DummyCore()
    audio = AudioCoordinator(core)
    audio.record_launch("Sea Dart SAM")
    entry = audio.state["last_launch"]
    assert entry["weapon"] == "sea-dart-sam"
    assert isinstance(entry["ts"], float)
