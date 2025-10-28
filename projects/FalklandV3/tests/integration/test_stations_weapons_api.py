import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from falklandv3.adapters.api.server import create_app


def test_weapons_station_endpoint_reports_inventory():
    app = create_app()
    client = TestClient(app)

    res = client.get("/api/stations/weapons")
    assert res.status_code == 200
    payload = res.json()
    assert {"slots", "armed_count", "safe_count", "total_slots", "low_ammo_count"} <= payload.keys()
    assert isinstance(payload["slots"], list)
    assert "cooldown_remaining_s" in payload["slots"][0]
    assert payload["slots"][0]["ammo"] >= 0


def test_weapons_station_endpoint_updates_after_commands():
    app = create_app()
    client = TestClient(app)

    status = client.get("/api/stations/weapons").json()
    assert status["slots"], "expected default slots"
    name = status["slots"][0]["name"]

    client.post("/api/weapons/arm", json={"name": name})
    updated = client.get("/api/stations/weapons").json()
    armed_slot = next(slot for slot in updated["slots"] if slot["name"] == name)
    assert armed_slot["armed"] is True
    assert "ammo" in armed_slot
    assert updated["armed_count"] >= status["armed_count"]
