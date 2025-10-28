import json
from pathlib import Path

import pytest

pytest.importorskip("pydantic")

from falklandv3.api.schemas.status import StatusSnapshot
from falklandv3.services.runtime import GameRuntime


@pytest.fixture()
def runtime_snapshot():
    runtime = GameRuntime()
    runtime.tick(0.1)
    return runtime.snapshot()


def test_status_snapshot_validates_runtime_payload(runtime_snapshot):
    status = StatusSnapshot.model_validate(runtime_snapshot)
    assert status.ship.hud.startswith("Ship")
    assert status.weather.sea_state >= 0.0
    assert status.radio.messages is not None
    assert status.wave.label
    assert status.mission.decision is None or isinstance(status.mission.decision, dict)
    assert status.health.assets is not None
    assert status.cap.harriers is not None


def test_schema_artifact_matches_generated():
    schema = StatusSnapshot.model_json_schema()
    artifact_path = Path(__file__).resolve().parents[4] / "docs" / "contracts" / "status.schema.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert schema["properties"].keys() == artifact["properties"].keys()
