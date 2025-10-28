import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from falklandv3.adapters.api.server import create_app


def test_radio_station_endpoint_returns_messages():
    app = create_app()
    client = TestClient(app)

    res = client.get("/api/stations/radio")
    assert res.status_code == 200
    payload = res.json()
    assert {"messages", "summaries", "total_messages"} <= payload.keys()
    assert isinstance(payload["messages"], list)
    assert isinstance(payload["summaries"], list)


def test_radio_station_endpoint_limit_param():
    app = create_app()
    client = TestClient(app)

    # Trigger some radio chatter through CAP damage/mission events
    client.post("/api/cap/launch")
    client.get("/api/status")

    res = client.get("/api/stations/radio", params={"limit": 1})
    assert res.status_code == 200
    payload = res.json()
    assert len(payload["messages"]) <= 1
