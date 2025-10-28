import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from falklandv3.adapters.api.server import create_app
from falklandv3.core.mission import MissionLoader, MissionManager
from pathlib import Path

from falklandv3.adapters.api.dependencies import runtime_dep
from falklandv3.core.mission import MissionLoader, MissionManager


def test_status_snapshot_roundtrip():
    app = create_app()
    client = TestClient(app)

    res = client.get("/api/status")
    assert res.status_code == 200
    payload = res.json()
    assert "ship" in payload
    assert "radar" in payload
    assert "mission" in payload
    assert "cap" in payload
    assert "weapons" in payload
    assert "audio" in payload
    assert "health" in payload
    ship = payload["ship"]
    assert {"cell", "x_nm", "y_nm", "heading_deg", "speed_kts", "hud"} <= ship.keys()
    radar = payload["radar"]
    assert "contacts" in radar
    assert "locked_contact_id" in radar
    assert isinstance(radar["contacts"], list)
    for contact in radar["contacts"]:
        assert "cell" in contact
    wave = payload["wave"]
    assert {"label", "elapsed_s", "duration_s"} <= wave.keys()
    mission = payload["mission"]
    assert {"id", "label", "status", "elapsed_s", "time_left_s", "decision"} <= mission.keys()
    cap = payload["cap"]
    assert {"status", "sorties", "time_in_status_s", "harriers"} <= cap.keys()
    weapons = payload["weapons"]
    assert "slots" in weapons
    assert isinstance(weapons["slots"], list)
    first_slot = weapons["slots"][0]
    assert "cooldown_remaining_s" in first_slot
    audio = payload["audio"]
    assert "events" in audio
    assert isinstance(audio["events"], list)
    assert "shots_in_flight" in audio
    assert isinstance(audio["shots_in_flight"], list)
    weather = payload["weather"]
    assert {"wind_dir_deg", "wind_speed_kts", "sea_state"} <= weather.keys()
    radio = payload["radio"]
    assert "messages" in radio
    history = payload["nav_history"]
    assert "entries" in history
    cap_history = payload["cap_history"]
    assert "entries" in cap_history
    health = payload["health"]
    assert "assets" in health


def test_health_endpoint():
    app = create_app()
    client = TestClient(app)

    res = client.get("/api/health")
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "ok"
    assert "build" in payload


def test_course_and_speed_commands_update_snapshot():
    app = create_app()
    client = TestClient(app)

    res = client.post("/api/nav/course", json={"heading_deg": 135})
    assert res.status_code == 200
    assert res.json()["ship"]["heading_deg"] == 135

    res = client.post("/api/nav/speed", json={"speed_kts": 20})
    assert res.status_code == 200
    assert res.json()["ship"]["speed_kts"] == 20


def test_cap_launch_endpoint_advances_sorties():
    app = create_app()
    client = TestClient(app)

    baseline = client.get("/api/status").json()["cap"]
    assert baseline["status"] == "ready"

    res = client.post("/api/cap/launch")
    assert res.status_code == 200
    payload = res.json()["cap"]
    assert payload["status"] == "launched"
    assert payload["sorties"] == baseline["sorties"] + 1


def test_nav_history_endpoint():
    app = create_app()
    client = TestClient(app)

    client.post("/api/nav/course", json={"heading_deg": 200})
    res = client.get("/api/nav/history")
    assert res.status_code == 200
    history = res.json()
    assert history["entries"]


def test_weapon_arming_endpoints_toggle_state():
    app = create_app()
    client = TestClient(app)

    status = client.get("/api/status").json()
    slots = status["weapons"]["slots"]
    assert slots, "expected default weapon slots"
    name = slots[0]["name"]

    res = client.post("/api/weapons/arm", json={"name": name})
    assert res.status_code == 200
    response_payload = res.json()
    updated = response_payload["weapons"]["slots"]
    armed_slot = next(s for s in updated if s["name"] == name)
    assert armed_slot["state"] == "Armed"
    assert response_payload["audio"]["events"], "Arming should emit audio event"

    res = client.post("/api/weapons/fire", json={"name": name, "mode": "test"})
    if res.status_code == 409:
        # Some weapons cannot fire without target; acceptable for this smoke test.
        pass
    else:
        assert res.status_code == 200

    res = client.post("/api/weapons/safe", json={"name": name})
    assert res.status_code == 200
    reverted = res.json()["weapons"]["slots"]
    reverted_slot = next(s for s in reverted if s["name"] == name)
    assert reverted_slot["state"] == "Safe"


def test_mission_decision_endpoint():
    app = create_app()
    client = TestClient(app)

    missions_dir = Path(__file__).resolve().parents[2] / "falklandv3" / "data" / "missions"
    runtime_dep.runtime.mission = MissionManager(
        MissionLoader(missions_dir),
        active_id="protect_hermes",
        health_provider=runtime_dep.runtime.health.lives,
    )
    runtime_dep.runtime.state.update_mission(runtime_dep.runtime.mission.snapshot())
    runtime_dep.runtime.mission.consume_announce()

    try:
        runtime_dep.runtime.damage_asset("hermes", 5)

        status = client.get("/api/status").json()
        decision = status["mission"].get("decision")
        assert decision
        assert decision["status"] == "pending"

        res = client.post(
            "/api/mission/decision",
            json={"decision_id": decision.get("id", ""), "choice": "accept"},
        )
        assert res.status_code == 200
        resolved = res.json()["mission"]["decision"]
        assert resolved["status"] == "resolved"
        assert resolved["choice"] == "accept"
    finally:
        runtime_dep.runtime.repair_asset("hermes", 5)
        runtime_dep.runtime.mission = MissionManager(
            MissionLoader(missions_dir),
            active_id="example",
            health_provider=runtime_dep.runtime.health.lives,
        )
        runtime_dep.runtime.state.update_mission(runtime_dep.runtime.mission.snapshot())
        runtime_dep.runtime.mission.consume_announce()


def test_background_loop_advances_runtime_state():
    app = create_app()
    client = TestClient(app)

    try:
        first = client.get("/api/status").json()
        time.sleep(1.2)
        second = client.get("/api/status").json()
        assert second["mission"]["elapsed_s"] > first["mission"]["elapsed_s"]
        assert second["wave"]["elapsed_s"] >= first["wave"]["elapsed_s"]
    finally:
        client.close()
