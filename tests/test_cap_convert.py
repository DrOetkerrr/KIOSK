from __future__ import annotations

import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from projects.falklandV2.subsystems.hermes_cap import HermesCAP


DATA_DIR = ROOT / "projects" / "falklandV2" / "data"


def _make_cap() -> HermesCAP:
    return HermesCAP(DATA_DIR)


def test_convert_to_cap_requires_transit():
    cap = _make_cap()
    now = 120.0
    res = cap.request_cap_to_cell(
        target_cell="AH35",
        distance_nm=12.0,
        now=now,
        origin_xy=(16.0, 37.0),
        origin_cell="AQ37",
        mission_kind="intercept",
    )
    assert res["ok"] is True
    mission = cap.missions[-1]

    # Simulate mid-flight progress toward AH35.
    mission.ts['eta_onstation'] = 600.0
    mission.ts['airborne'] = now

    convert_now = 180.0
    result = cap.convert_to_cap(mission.id, "AP20", now=convert_now)
    assert result["ok"] is True

    mission = cap.missions[-1]
    assert mission.status == 'airborne'
    eta = mission.ts.get('eta_onstation')
    assert eta is not None and eta > convert_now
    # Expected distance from current position (roughly halfway back to AQ37)
    current_xy = cap._mission_position_xy(mission, convert_now)
    target_xy = cap._target_xy("AP20")
    distance_nm = math.hypot(target_xy[0] - current_xy[0], target_xy[1] - current_xy[1])
    expected_eta = convert_now + math.ceil(distance_nm / max(mission.cruise_speed_kts / 3600.0, 0.05))
    assert math.isclose(eta, expected_eta, rel_tol=0.0, abs_tol=1.0)
    assert mission.ts.get('vector_start_xy') is not None
    assert result.get('eta_seconds') == int(math.ceil(distance_nm / max(mission.cruise_speed_kts / 3600.0, 0.05)))


def test_convert_to_cap_immediate_onstation():
    cap = _make_cap()
    res = cap.request_cap_to_cell(
        target_cell="AQ37",
        distance_nm=0.0,
        now=300.0,
        origin_xy=(16.0, 37.0),
        origin_cell="AQ37",
        mission_kind="intercept",
    )
    assert res["ok"] is True
    mission = cap.missions[-1]

    result = cap.convert_to_cap(mission.id, "AQ37", now=360.0)
    assert result["ok"] is True
    mission = cap.missions[-1]
    assert mission.status == 'onstation'
    assert mission.ts.get('eta_onstation') == 360.0
    assert mission.ts.get('etd_rtb') == 360.0 + mission.onstation_s
    assert result.get('eta_seconds') == 0
