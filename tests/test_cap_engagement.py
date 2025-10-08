from __future__ import annotations

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
