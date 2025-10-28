from fastapi.testclient import TestClient

from dataclasses import replace

from falklandv3.adapters.api.server import create_app
from falklandv3.adapters.api.dependencies import runtime_dep


def test_nav_course_requires_api_key():
    runtime_dep.runtime.settings = replace(runtime_dep.runtime.settings, api_key="secret")
    app = create_app()
    client = TestClient(app)

    resp = client.post("/api/nav/course", json={"heading_deg": 123})
    assert resp.status_code == 401

    ok = client.post(
        "/api/nav/course",
        json={"heading_deg": 123},
        headers={"X-Falkland-Key": "secret"},
    )
    assert ok.status_code == 200

    # cleanup for other tests
    runtime_dep.runtime.settings = replace(runtime_dep.runtime.settings, api_key=None)
