from __future__ import annotations

from typing import Any, Dict

import pytest

from projects.falklandV2.webdash import app as web_app


@pytest.fixture(scope="module")
def client():
    with web_app.test_client() as client:
        yield client


def _get_json(client, path: str):
    response = client.get(path)
    try:
        payload = response.get_json(force=True)  # type: ignore[assignment]
    except Exception as exc:  # pragma: no cover - easier debug signal
        pytest.fail(f"{path} invalid JSON: {exc!r}")
    return response, payload


def _assert_ok(response, payload: Dict[str, Any], path: str, expect_schema: str | None = None):
    assert response.status_code == 200, f"{path} status {response.status_code}, body={payload}"
    assert isinstance(payload, dict), f"{path} payload type {type(payload)}"
    assert payload.get("ok", True) is not False, f"{path} reported failure: {payload}"
    if expect_schema:
        header_version = response.headers.get("X-Schema-Version")
        payload_version = payload.get("schemaVersion")
        assert header_version == expect_schema, f"{path} header schema {header_version!r} != {expect_schema}"
        assert payload_version == expect_schema, f"{path} payload schema {payload_version!r} != {expect_schema}"


def test_status_endpoint(client):
    response, payload = _get_json(client, "/api/status")
    _assert_ok(response, payload, "/api/status", expect_schema="1.0.0")
    # ensure critical keys exist to avoid silent regressions
    assert "state" in payload and isinstance(payload["state"], dict)
    assert "capabilities" in payload


@pytest.mark.parametrize(
    "command",
    [
        "/radar scan",
        "/radar unlock",
    ],
)
def test_command_endpoint(client, command: str):
    response, payload = _get_json(client, f"/api/command?cmd={command}")
    _assert_ok(response, payload, f"/api/command?cmd={command}", expect_schema="1.0.0")
