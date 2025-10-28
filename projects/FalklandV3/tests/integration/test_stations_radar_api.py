import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from falklandv3.adapters.api.server import create_app


def test_radar_station_endpoint_exposes_contacts():
    app = create_app()
    client = TestClient(app)

    res = client.get("/api/stations/radar")
    assert res.status_code == 200
    payload = res.json()
    assert {"contacts", "hostile_count", "friendly_count", "max_contacts", "locked_contact_id"} <= payload.keys()
    assert isinstance(payload["contacts"], list)
    for contact in payload["contacts"]:
        assert {"id", "label", "range_nm", "bearing_deg", "hostile", "priority", "cell"} <= contact.keys()
    assert payload["max_contacts"] >= len(payload["contacts"])
