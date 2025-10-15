from __future__ import annotations

import copy
import time
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from projects.falklandV2 import webdash


@pytest.fixture(autouse=True)
def _reset_runtime():
    client = webdash.app.test_client()
    client.post("/diag/reset")
    yield


def _clear_radio_queue() -> None:
    with webdash.STATE_LOCK:
        webdash.RADIO_QUEUE.clear()


def _radio_queue_snapshot() -> list[dict]:
    with webdash.STATE_LOCK:
        return [dict(entry) for entry in webdash.RADIO_QUEUE]


def test_resupply_launch_sets_enroute_state(monkeypatch: pytest.MonkeyPatch) -> None:
    base_time = 1_000.0
    monkeypatch.setattr("projects.falklandV2.routes.resupply.time.time", lambda: base_time)
    client = webdash.app.test_client()

    _clear_radio_queue()
    resp = client.post("/resupply/launch", json={"eta_s": 120})
    data = resp.get_json()
    assert data["ok"] is True

    state = webdash.RESUPPLY
    assert state["active"] is True
    assert state["stage"] == "enroute"
    assert state["started_ts"] == pytest.approx(base_time)
    assert state["eta_ts"] == pytest.approx(base_time + 120)


def test_resupply_launch_emits_radio_callout(monkeypatch: pytest.MonkeyPatch) -> None:
    base_time = 5_000.0
    monkeypatch.setattr("projects.falklandV2.routes.resupply.time.time", lambda: base_time)
    client = webdash.app.test_client()

    _clear_radio_queue()
    resp = client.post("/resupply/launch", json={})
    assert resp.status_code == 200

    entries = _radio_queue_snapshot()
    launch_entries = [entry for entry in entries if entry.get("event") == "resupply.launch"]
    assert launch_entries, "Sea King launch should enqueue radio traffic"
    audio_path = str(launch_entries[-1].get("file", "")).lower()
    assert audio_path.endswith("seaking_taking_off.wav")
    assert launch_entries[-1].get("role") == "Pilot"


def test_resupply_complete_refills_ammo(monkeypatch: pytest.MonkeyPatch) -> None:
    client = webdash.app.test_client()
    # Seed state to mimic landing phase
    webdash.RESUPPLY.update(
        {
            "active": True,
            "stage": "landing",
            "eta_ts": time.time() - 10.0,
            "ready_announced": True,
        }
    )

    base_time = 2_000.0
    monkeypatch.setattr("projects.falklandV2.routes.resupply.time.time", lambda: base_time)

    from projects.falklandV2.subsystems import webcore

    baseline_defaults = {**webcore.WEAP_DEFAULT_AMMO, **webcore._ammo_defaults_from_ship()}  # type: ignore[attr-defined]
    current_ammo = {k: max(0, int(v) - 5) for k, v in baseline_defaults.items()}

    monkeypatch.setattr("projects.falklandV2.subsystems.webcore.load_ammo", lambda: current_ammo.copy())
    saved_ammo: dict[str, int] = {}

    def _fake_save(payload: dict[str, int]) -> None:
        saved_ammo.update(payload)

    monkeypatch.setattr("projects.falklandV2.subsystems.webcore.save_ammo", _fake_save)

    resp = client.post("/resupply/complete")
    data = resp.get_json()
    assert data["ok"] is True

    state = webdash.RESUPPLY
    assert state["active"] is False
    assert state["stage"] == "complete"

    assert saved_ammo, "resupply should persist refreshed ammo"
    for weapon, baseline in baseline_defaults.items():
        assert saved_ammo[weapon] >= baseline


def test_resupply_complete_emits_radio_callout(monkeypatch: pytest.MonkeyPatch) -> None:
    client = webdash.app.test_client()
    webdash.RESUPPLY.update(
        {
            "active": True,
            "stage": "landing",
            "eta_ts": time.time() - 1.0,
            "ready_announced": True,
        }
    )
    monkeypatch.setattr("projects.falklandV2.routes.resupply.time.time", lambda: 6_000.0)
    monkeypatch.setattr("projects.falklandV2.subsystems.webcore.load_ammo", lambda: {})
    monkeypatch.setattr("projects.falklandV2.subsystems.webcore.save_ammo", lambda payload: None)

    _clear_radio_queue()
    resp = client.post("/resupply/complete")
    assert resp.status_code == 200

    entries = _radio_queue_snapshot()
    complete_entries = [entry for entry in entries if entry.get("event") == "resupply.complete"]
    assert complete_entries, "Sea King completion should enqueue radio traffic"
    audio_path = str(complete_entries[-1].get("file", "")).lower()
    assert audio_path.endswith("seaking_ready_resupply.wav")
    assert complete_entries[-1].get("role") == "Pilot"
