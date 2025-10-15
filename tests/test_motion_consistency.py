from __future__ import annotations

import math
from pathlib import Path

import pytest

from projects.falklandV2.core.engine import Engine
from projects.falklandV2.engine_adapter import world_to_cell
from projects.falklandV2.radar import Contact, HOSTILE_SPEED_SCALE, WORLD_N
from projects.falklandV2.subsystems.hermes_cap import HermesCAP


DATA_DIR = Path("projects/falklandV2/data")


def _cap_instance() -> HermesCAP:
    return HermesCAP(DATA_DIR)


def _nm_expected(speed_kts: float, dt_s: float) -> float:
    return speed_kts * (dt_s / 3600.0)


def test_engine_ship_motion_matches_declared_speed() -> None:
    eng = Engine()
    eng.set_course(90.0)
    eng.set_speed(18.0)  # 18 kts → 0.3 nm per minute
    start_x, start_y = eng.ship.x, eng.ship.y
    dt_s = 180.0  # 3 minutes
    eng.tick(dt_s)
    end_x, end_y = eng.ship.x, eng.ship.y
    travelled_nm = math.hypot(end_x - start_x, end_y - start_y)
    expected_nm = _nm_expected(18.0, dt_s)
    assert math.isclose(travelled_nm, expected_nm, rel_tol=1e-3, abs_tol=1e-3)
    # Ensure ship stays inside world bounds when advancing
    assert 0.0 <= end_x <= float(WORLD_N)
    assert 0.0 <= end_y <= float(WORLD_N)


def test_cap_transit_respects_speed_and_cell_path() -> None:
    cap = _cap_instance()
    origin_xy = (17.0, 19.0)  # Matches Hermes initial position in Engine
    origin_cell = world_to_cell(*origin_xy)
    target_cell = "AW19"

    # Compute range in world nm using cell centre (consistent with radar visuals)
    from projects.falklandV2.engine_adapter import cell_to_world

    target_xy = cell_to_world(target_cell)
    distance_nm = math.hypot(target_xy[0] - origin_xy[0], target_xy[1] - origin_xy[1])
    now = 1_000.0
    res = cap.request_cap_to_cell(
        target_cell,
        distance_nm=distance_nm,
        origin_xy=origin_xy,
        origin_cell=origin_cell,
        now=now,
    )
    assert res["ok"], res
    mission = cap.missions[-1]
    assert mission.status == "airborne"

    expected_outbound = distance_nm / mission.cruise_speed_kts * 3600.0
    assert math.isclose(mission.outbound_s, expected_outbound, rel_tol=0.05)

    total_duration = max(1.0, (mission.ts["eta_onstation"] - mission.ts["airborne"]))
    distance_world = math.hypot(target_xy[0] - origin_xy[0], target_xy[1] - origin_xy[1])
    elapsed = 0.0
    for step in range(1, int(total_duration) + 1):
        elapsed = float(step)
        cap.tick(now=now + elapsed)
        if step % 5 != 0 and step != int(total_duration):
            continue
        snap = cap.snapshot(now=now + elapsed)
        current = next(m for m in snap["missions"] if m["id"] == mission.id)
        pos = current["position_xy"]
        travelled = math.hypot(pos[0] - origin_xy[0], pos[1] - origin_xy[1])
        progress = min(elapsed, total_duration) / total_duration
        expected = distance_world * progress
        assert travelled == pytest.approx(expected, rel=0.05, abs=0.25)

    # One final tick to reach station
    final_time = now + total_duration + 0.1
    cap.tick(now=final_time)
    final_snap = cap.snapshot(now=final_time)
    final = next(m for m in final_snap["missions"] if m["id"] == mission.id)
    assert final["status"] == "onstation"
    final_xy = final["position_xy"]
    final_cell = world_to_cell(final_xy[0], final_xy[1])
    assert final_cell == target_cell
    assert final.get("range_nm") == pytest.approx(0.0, abs=0.25)


@pytest.mark.parametrize(
    "allegiance, meta",
    [
        ("Friendly", {}),
        ("Hostile", {"role": "attacker"}),
    ],
)
def test_air_contact_speed_matches_world_motion(allegiance: str, meta: dict[str, object]) -> None:
    start_x, start_y = 10.0, 10.0
    course_deg = 90.0  # due east
    speed_kts = 600.0
    contact = Contact(
        id=1,
        name="Test Contact",
        allegiance=allegiance,
        x=start_x,
        y=start_y,
        course_deg=course_deg,
        speed_kts=speed_kts,
        threat="medium",
        meta=meta,
    )
    own_x, own_y = 40.0, 10.0  # keep desired heading aligned east
    dt_s = 120.0
    contact.tick(dt_s, own_x, own_y)
    expected_nm = _nm_expected(speed_kts * HOSTILE_SPEED_SCALE, dt_s)
    travelled = math.hypot(contact.x - start_x, contact.y - start_y)
    assert travelled == pytest.approx(expected_nm, rel=1e-3, abs=1e-3)

    # Convert to cell label and ensure displacement matches integer rounding
    start_cell = world_to_cell(start_x, start_y)
    end_cell = world_to_cell(contact.x, contact.y)
    assert start_cell != end_cell  # should have crossed several cells over 2 minutes


def test_exocet_contact_uses_declared_speed_profile() -> None:
    start_x, start_y = 22.0, 12.0
    course_deg = 180.0  # southbound
    speed_kts = 450.0
    missile = Contact(
        id=77,
        name="MM38 Exocet",
        allegiance="Hostile",
        x=start_x,
        y=start_y,
        course_deg=course_deg,
        speed_kts=speed_kts,
        threat="high",
        meta={"kind": "missile"},
    )
    dt_s = 60.0
    own_x, own_y = start_x, start_y + 50.0  # irrelevant for missiles
    missile.tick(dt_s, own_x, own_y)
    expected_nm = _nm_expected(speed_kts * HOSTILE_SPEED_SCALE, dt_s)
    travelled = math.hypot(missile.x - start_x, missile.y - start_y)
    assert travelled == pytest.approx(expected_nm, rel=1e-3, abs=1e-3)

    start_cell = world_to_cell(start_x, start_y)
    end_cell = world_to_cell(missile.x, missile.y)
    # One minute at 450 kts (game scale) should move multiple cells toward the target.
    assert start_cell != end_cell
