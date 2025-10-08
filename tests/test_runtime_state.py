from __future__ import annotations

import json
from pathlib import Path

import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projects.falklandV2.runtime_state import StateRepository


@pytest.fixture
def dummy_core(tmp_path: Path):
    class _Core:
        LOG_DIR = tmp_path / "logs"
        FLIGHT_PATH = tmp_path / "logs" / "flight.jsonl"
        FLIGHT_MAX_BYTES = 1024
        DATA_DIR = tmp_path / "data"
        STATE_DIR = tmp_path / "state"
        AMMO_PATH = tmp_path / "state" / "ammo.json"
        ARMING_PATH = tmp_path / "state" / "arming.json"
        WEAP_CATALOG_PATH = tmp_path / "data" / "weapons_catalog.json"
        CONTACTS_PATH = tmp_path / "data" / "contacts.json"
        CREW_PATH = tmp_path / "data" / "crew.json"
        ALARM_CFG_PATH = tmp_path / "data" / "alarms.json"
        HEALTH_PATH = tmp_path / "state" / "health.json"
        TTS_DIR = tmp_path / "state" / "tts"
        VOICE_EVENTS_PATH = tmp_path / "data" / "voice_events.json"
        SKIRMISHES_PATH = tmp_path / "state" / "skirmishes.json"
        ROADMAP_PATH = tmp_path / "state" / "roadmap.json"
        VOICES_DIR = tmp_path / "state" / "voices"

    return _Core()


def test_state_repository_paths(dummy_core):
    repo = StateRepository(dummy_core)
    assert repo.log_dir == Path(dummy_core.LOG_DIR)
    assert repo.flight_path == Path(dummy_core.FLIGHT_PATH)
    assert repo.flight_max_bytes == int(dummy_core.FLIGHT_MAX_BYTES)
    assert repo.ammo_path == Path(dummy_core.AMMO_PATH)
    assert repo.weapons_catalog_path == Path(dummy_core.WEAP_CATALOG_PATH)
    assert repo.voices_dir == Path(dummy_core.VOICES_DIR)


def test_state_repository_load_save_json(tmp_path: Path, dummy_core):
    repo = StateRepository(dummy_core)
    target = tmp_path / "example.json"

    # Missing file returns default
    assert repo.load_json(target, default={"ok": True}) == {"ok": True}

    data = {"hello": "world", "count": 5}
    repo.save_json(target, data)
    assert json.loads(target.read_text(encoding="utf-8")) == data

    # Round-trip loader without default
    assert repo.load_json(target, default=None) == data


def test_state_repository_domain_helpers(dummy_core):
    repo = StateRepository(dummy_core)

    ammo = {"Gun": 5}
    repo.save_ammo(ammo)
    assert repo.load_ammo() == ammo

    arming = {"Gun": "Armed"}
    repo.save_arming(arming)
    assert repo.load_arming() == arming
