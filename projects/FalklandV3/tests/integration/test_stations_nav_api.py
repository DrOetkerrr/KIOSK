import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from falklandv3.adapters.api.server import create_app


def test_nav_station_endpoint_exposes_projection():
    app = create_app()
    client = TestClient(app)

    res = client.get("/api/stations/nav")
    assert res.status_code == 200
    payload = res.json()
    assert {"hud", "heading_deg", "speed_kts", "cell", "x_nm", "y_nm"} <= payload.keys()
    assert "history" in payload and isinstance(payload["history"], list)
    assert "history_total" in payload
    assert "tick_dt" in payload


def test_nav_station_endpoint_limit_query_param():
    app = create_app()
    client = TestClient(app)

    client.post("/api/nav/course", json={"heading_deg": 45})
    client.post("/api/nav/speed", json={"speed_kts": 22})
    client.post("/api/nav/course", json={"heading_deg": 220})

    res = client.get("/api/stations/nav", params={"limit": 1})
    assert res.status_code == 200
    payload = res.json()
    assert len(payload["history"]) <= 1
