from __future__ import annotations

import math
import random
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from projects.falklandV2 import webdash
from projects.falklandV2.subsystems.hermes_cap import HermesCAP
from projects.falklandV2.engine_adapter import cell_to_world, world_to_cell


DATA_DIR = Path(__file__).resolve().parents[1] / "projects" / "falklandV2" / "data"


class _Target:
    def __init__(self, name: str = "Bandit", klass: str = "Aircraft") -> None:
        self.name = name
        setattr(self, "class", klass)
        self.type = klass


def _make_cap() -> HermesCAP:
    return HermesCAP(DATA_DIR)


def test_auto_engage_hits_air_target(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _make_cap()

    res = cap.request_cap_to_cell(
        target_cell="K10",
        distance_nm=8.0,
        mission_kind="intercept",
        loadout="aim9",
    )
    assert res["ok"] is True

    mission = cap.missions[-1]
    mission.status = "onstation"
    mission.ts["onstation"] = time.time() - 5
    mission.ts["etd_rtb"] = time.time() + 300
    mission.missiles_left = 4
    cap.set_permission(mission.id, True)

    target = _Target()
    cap.bind_target_resolver(lambda cid: target if cid == 42 else None)

    hits: list[tuple[int, str]] = []
    cap.bind_hit_callback(lambda cid, _name, klass, ctx=None: hits.append((cid, klass)))

    monkeypatch.setattr(random, "random", lambda: 0.0)

    result = cap.auto_engage(3.0, 42, now=time.time())

    assert result is not None
    assert result["hit"] is True
    assert result["shots"] == 1
    assert mission.missiles_left == 3
    assert hits == [(42, "Aircraft")]


def _arm_onstation(cap: HermesCAP, *, loadout: str = "aim9") -> "CAPMission":
    res = cap.request_cap_to_cell(
        target_cell="K10",
        distance_nm=8.0,
        mission_kind="intercept",
        loadout=loadout,
    )
    assert res["ok"] is True
    mission = cap.missions[-1]
    mission.status = "onstation"
    mission.ts["onstation"] = time.time() - 30
    mission.ts["etd_rtb"] = time.time() + 300
    mission.missiles_left = 4
    return mission


def test_auto_engage_requires_permission() -> None:
    cap = _make_cap()
    mission = _arm_onstation(cap)
    mission.permission_required = True
    mission.permission_authorized = False

    result = cap.auto_engage(3.0, 99, now=time.time())

    assert result is None
    assert mission.missiles_left == 4


def test_auto_engage_out_of_range() -> None:
    cap = _make_cap()
    mission = _arm_onstation(cap)
    cap.set_permission(mission.id, True)

    result = cap.auto_engage(6.5, 99, now=time.time())

    assert result is None
    assert mission.missiles_left == 4


def test_auto_engage_wrong_payload_emits_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict]] = []
    cap = HermesCAP(DATA_DIR, event_hook=lambda eid, payload: events.append((eid, dict(payload or {}))))
    mission = _arm_onstation(cap, loadout="bombs")
    mission.missiles_left = 2
    cap.set_permission(mission.id, True)

    target = _Target(name="Bandit", klass="Aircraft")
    cap.bind_target_resolver(lambda cid: target if cid == 7 else None)

    before = mission.missiles_left
    result = cap.auto_engage(0.5, 7, now=time.time())

    assert result is None
    assert mission.missiles_left == before
    assert any(eid == "cap.engage.denied" and evt.get("reason") == "wrong_payload" for eid, evt in events)


def test_auto_engage_bombs_surface_target(monkeypatch: pytest.MonkeyPatch) -> None:
    hits: list[tuple[int, str]] = []
    cap = HermesCAP(DATA_DIR, event_hook=lambda *a, **k: None)
    mission = _arm_onstation(cap, loadout="bombs")
    mission.missiles_left = 2
    cap.set_permission(mission.id, True)
    cap.bind_hit_callback(lambda cid, _name, klass, ctx=None: hits.append((cid, klass)))

    target = _Target(name="Belgrano", klass="Ship")
    cap.bind_target_resolver(lambda cid: target if cid == 55 else None)

    result = cap.auto_engage(0.8, 55, now=time.time())

    assert result is not None
    assert result["hit"] is True
    assert hits == [(55, "Ship")]
    assert mission.missiles_left == 1


def test_auto_engage_second_shot_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _make_cap()
    mission = _arm_onstation(cap)
    cap.set_permission(mission.id, True)
    target = _Target()
    cap.bind_target_resolver(lambda cid: target if cid == 42 else None)

    outcomes = iter([1.0, 0.0])  # miss first, hit second
    monkeypatch.setattr(random, "random", lambda: next(outcomes))

    hits: list[tuple[int, str]] = []
    cap.bind_hit_callback(lambda cid, _name, klass, ctx=None: hits.append((cid, klass)))

    result = cap.auto_engage(3.0, 42, now=time.time())

    assert result is not None
    assert result["shots"] == 2
    assert result["hit"] is True
    assert mission.missiles_left == 2
    assert hits == [(42, "Aircraft")]


def test_auto_engage_respects_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _make_cap()
    mission = _arm_onstation(cap)
    cap.set_permission(mission.id, True)
    target = _Target()
    cap.bind_target_resolver(lambda cid: target if cid == 5 else None)

    monkeypatch.setattr(random, "random", lambda: 0.0)
    now = time.time()
    first = cap.auto_engage(3.0, 5, now=now)
    assert first is not None

    mission.missiles_left = 4  # reset for clarity
    second = cap.auto_engage(3.0, 5, now=now + 1.0)
    assert second is None

    third = cap.auto_engage(3.0, 5, now=now + mission.engagement_cooldown_s + 1.0)
    assert third is not None


def test_cap_scramble_cooldown_controls_second_launch() -> None:
    cap = _make_cap()
    cap.scramble_cooldown_s = 5
    cap.min_launch_interval_s = 5
    cap.ready_pairs = 2
    cap.airframe_pool_total = 4

    now = 1_000.0
    first = cap.request_cap_to_cell(
        target_cell="K10",
        distance_nm=8.0,
        mission_kind="cap",
        loadout="aim9",
        now=now,
    )
    assert first["ok"] is True
    first_mission = cap.missions[-1]
    assert first_mission.status == "airborne"
    assert cap.last_scramble == pytest.approx(now)
    readiness = cap.readiness(now=now)
    assert readiness["cooldown_s"] == 5
    assert readiness["available"] is False

    second = cap.request_cap_to_cell(
        target_cell="K12",
        distance_nm=9.0,
        mission_kind="cap",
        loadout="aim9",
        now=now,
    )
    assert second["ok"] is True
    second_mission = cap.missions[-1]
    assert second_mission is not first_mission
    assert second_mission.status == "queued"
    assert second_mission.deck_cycle_s == 5
    assert pytest.approx(second_mission.ts.get("launch_ready"), rel=0.01) == now + 5
    assert cap.readiness(now=now)["queued_count"] == 1

    cap.tick(now=now + 4.0)
    assert second_mission.status == "queued"
    assert pytest.approx(second_mission.ts.get("launch_ready"), rel=0.01) == now + 5

    cap.tick(now=now + 5.0)
    assert second_mission.status == "airborne"
    assert "launch_ready" not in second_mission.ts
    assert cap.last_scramble == pytest.approx(now + 5.0)
    assert cap.readiness(now=now + 5.0)["queued_count"] == 0


def test_cap_launch_voice_triggers_on_actual_takeoff() -> None:
    cap = _make_cap()
    events: list[tuple[str, dict]] = []
    cap.bind_voice_hook(lambda event_id, data: events.append((event_id, dict(data or {}))))
    cap.scramble_cooldown_s = 10
    cap.min_launch_interval_s = 0
    cap.ready_pairs = 2
    cap.airframe_pool_total = 4
    cap.last_scramble = 100.0

    now = 100.0
    res = cap.request_cap_to_cell(
        target_cell="AR06",
        distance_nm=12.0,
        station_minutes=10.0,
        radius_nm=10.0,
        mission_kind="cap",
        loadout="aim9",
        now=now,
    )
    assert res["ok"] is True
    assert events == []

    mission = cap.missions[-1]
    assert mission.status == "queued"
    delay_s = mission.deck_cycle_s
    assert delay_s > 0

    cap.tick(now=now + delay_s)

    assert events, "voice call should fire once aircraft actually launch"
    event_id, payload = events[0]
    assert event_id == "pilot.cap.launch"
    assert payload.get("mission_id") == mission.id


def test_cap_launch_voice_immediate_when_no_delay() -> None:
    cap = _make_cap()
    events: list[tuple[str, dict]] = []
    cap.bind_voice_hook(lambda event_id, data: events.append((event_id, dict(data or {}))))
    cap.scramble_cooldown_s = 0
    cap.min_launch_interval_s = 0
    cap.ready_pairs = 2
    cap.airframe_pool_total = 4
    cap.last_scramble = 0.0

    now = 50.0
    res = cap.request_cap_to_cell(
        target_cell="AR06",
        distance_nm=12.0,
        station_minutes=10.0,
        radius_nm=10.0,
        mission_kind="cap",
        loadout="aim9",
        now=now,
    )
    assert res["ok"] is True
    assert events, "voice call should fire immediately for an immediate launch"
    event_id, payload = events[0]
    assert event_id == "pilot.cap.launch"
    assert payload.get("mission_id") == cap.missions[-1].id


def test_cap_permission_timeout_forces_rtb() -> None:
    cap = _make_cap()
    now = 1_000.0
    origin_xy = (17.0, 19.0)
    target_cell = "AR06"
    tx, ty = cell_to_world(target_cell)
    distance_nm = math.hypot(tx - origin_xy[0], ty - origin_xy[1])
    res = cap.request_cap_to_cell(
        target_cell,
        distance_nm=distance_nm,
        origin_xy=origin_xy,
        origin_cell=world_to_cell(*origin_xy),
        now=now,
    )
    assert res["ok"], res
    mission = cap.missions[-1]
    mission.status = "onstation"
    mission.ts["onstation"] = now
    mission.ts["etd_rtb"] = now + 600.0
    mission.permission_required = True
    mission.permission_authorized = False
    mission.permission_hold_since_ts = now

    cap.permission_timeout_s = 60
    cap.tick(now=now + 61.0)
    assert mission.status == "rtb"


def _add_onstation_mission(cap: HermesCAP, cell: str, now: float, *, mission_kind: str = "cap") -> None:
    tx, ty = cell_to_world(cell)
    ox, oy = cell_to_world("AR05")
    distance_nm = math.hypot(tx - ox, ty - oy)
    res = cap.request_cap_to_cell(
        cell,
        distance_nm=distance_nm,
        origin_xy=(ox, oy),
        origin_cell="AR05",
        now=now,
        mission_kind=mission_kind,
    )
    assert res["ok"], res
    mission = cap.missions[-1]
    mission.status = "onstation"
    mission.ts["onstation"] = now
    mission.ts["etd_rtb"] = now + 600.0
    mission.permission_required = False
    mission.permission_authorized = True
    cap.ready_pairs += 1
    cap.airframe_pool_total += 2
    cap.last_scramble = now - cap.scramble_cooldown_s
    cap._deck_ready_ts = now  # type: ignore[attr-defined]


def test_cap_blocks_new_station_when_max_active() -> None:
    cap = _make_cap()
    cap.ready_pairs = 6
    cap.airframe_pool_total = 12
    now = 500.0
    for idx, cell in enumerate(["AR10", "AR12", "AR14"]):
        _add_onstation_mission(cap, cell, now + idx)
    result = cap.request_cap_to_cell(
        target_cell="AR16",
        distance_nm=8.0,
        origin_xy=(17.0, 19.0),
        origin_cell="AR05",
        now=now + 10.0,
    )
    assert result["ok"] is False
    assert result["message"] == "Max CAP stations active"


def test_cap_intercept_allowed_beyond_station_cap() -> None:
    cap = _make_cap()
    cap.ready_pairs = 6
    cap.airframe_pool_total = 12
    now = 800.0
    for idx, cell in enumerate(["AS10", "AS12", "AS14"]):
        _add_onstation_mission(cap, cell, now + idx)
    result = cap.request_cap_to_cell(
        target_cell="AT16",
        distance_nm=9.0,
        origin_xy=(17.0, 19.0),
        origin_cell="AR05",
        now=now + 5.0,
        mission_kind="intercept",
    )
    assert result["ok"] is True


def test_cap_surge_limit_blocks_after_four_pairs() -> None:
    cap = _make_cap()
    cap.ready_pairs = 8
    cap.airframe_pool_total = 16
    now = 900.0
    for idx, cell in enumerate(["AP08", "AP10", "AP12"]):
        _add_onstation_mission(cap, cell, now + idx)
    _add_onstation_mission(cap, "AP14", now + 3, mission_kind="intercept")
    result = cap.request_cap_to_cell(
        target_cell="AP18",
        distance_nm=10.0,
        origin_xy=(17.0, 19.0),
        origin_cell="AR05",
        now=now + 20.0,
        mission_kind="intercept",
    )
    assert result["ok"] is False
    assert result["message"] == "All CAP sorties committed"

def test_cap_follow_position_tracks_hermes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = webdash.app.test_client()
    client.post('/diag/reset')

    cell = 'AR17'
    x, y = webdash.cell_to_world(cell)
    runtime = webdash.RUNTIME
    runtime.engine.ship.x = float(x)
    runtime.engine.ship.y = float(y)
    runtime._update_engine_state_view()

    resp = client.post('/cap/launch_to', json={'cell': cell, 'follow': 'hermes', 'radius_nm': 10, 'station_minutes': 10, 'loadout': 'aim9'})
    assert resp.json.get('ok') is True
    assert resp.json['mission']['station_radius_nm'] == 10

    data = client.get('/api/status').json
    tasks = data.get('cap', {}).get('tasks', [])
    assert tasks, 'CAP task missing'
    assert tasks[0]['cur_cell'] == cell
