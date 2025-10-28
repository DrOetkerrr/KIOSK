import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from falklandv3.adapters.api.server import create_app


def test_engineering_station_endpoint_returns_assets_and_weather():
    app = create_app()
    client = TestClient(app)

    res = client.get("/api/stations/engineering")
    assert res.status_code == 200
    payload = res.json()
    assert {"assets", "critical_assets", "weather", "damage_alert"} <= payload.keys()
    assert isinstance(payload["assets"], list)


def test_engineering_station_endpoint_reflects_damage_flag():
    app = create_app()
    client = TestClient(app)

    baseline = client.get("/api/stations/engineering").json()
    assert "damage_alert" in baseline

    client.post("/api/mission/decision", json={"decision_id": "", "choice": ""})
    updated = client.get("/api/stations/engineering").json()
    assert "damage_alert" in updated
