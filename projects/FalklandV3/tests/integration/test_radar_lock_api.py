import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from falklandv3.adapters.api.server import create_app


def test_radar_lock_and_unlock_updates_snapshot():
    app = create_app()
    client = TestClient(app)

    status = client.get("/api/status").json()
    contacts = status["radar"]["contacts"]
    assert contacts, "expected default radar contacts"
    contact_id = contacts[0]["id"]

    lock_resp = client.post("/api/radar/lock", json={"contact_id": contact_id})
    assert lock_resp.status_code == 200
    locked_snapshot = lock_resp.json()
    assert locked_snapshot["radar"]["locked_contact_id"] == contact_id
    assert "shots_in_flight" in locked_snapshot["audio"]

    unlock_resp = client.post("/api/radar/unlock")
    assert unlock_resp.status_code == 200
    unlocked_snapshot = unlock_resp.json()
    assert unlocked_snapshot["radar"]["locked_contact_id"] is None


def test_radar_lock_invalid_contact_returns_not_found():
    app = create_app()
    client = TestClient(app)

    resp = client.post("/api/radar/lock", json={"contact_id": 9999})
    assert resp.status_code == 404
