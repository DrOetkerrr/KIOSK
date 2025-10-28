import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from falklandv3.adapters.api.server import create_app


def test_cap_status_and_reset():
    app = create_app()
    client = TestClient(app)

    status = client.get("/api/cap/status").json()
    assert status["status"] == "ready"

    client.post("/api/cap/launch")
    launched = client.get("/api/cap/status").json()
    assert launched["status"] == "launched"

    client.post("/api/cap/reset")
    reset = client.get("/api/cap/status").json()
    assert reset["status"] == "ready"
    assert reset["sorties"] == 0
