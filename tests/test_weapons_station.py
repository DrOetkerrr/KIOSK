from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Callable, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from projects.falklandV2 import webdash
from projects.falklandV2.routes import weapons as weapons_routes

from tests.test_bridge_events import (
    _reset_sim,
    _spawn_hostile_contact,
    _resolve_weapon_events,
)


ARMING_PATH = Path("projects") / "falklandV2" / "state" / "arming.json"


@pytest.fixture
def client():
    return webdash.app.test_client()


def _load_raw_arming() -> dict:
    return json.loads(ARMING_PATH.read_text(encoding="utf-8"))


def test_arm_route_transitions_and_repeats(monkeypatch: pytest.MonkeyPatch, client) -> None:
    _reset_sim(client)

    callbacks: List[Callable[[], None]] = []

    class TimerStub:
        def __init__(self, interval, func, *, daemon=True):
            callbacks.append(func)

        def start(self):
            return None

    monkeypatch.setattr(weapons_routes.threading, "Timer", lambda interval, func, daemon=True: TimerStub(interval, func))

    resp = client.post("/weapons/arm", json={"name": "Sea Dart SAM", "state": "Armed"})
    assert resp.status_code == 200
    assert resp.json["state"] == "Arming"
    assert callbacks, "arming timer not scheduled"

    callbacks.pop()()
    arming_map = _load_raw_arming()
    sea_dart = arming_map["Sea Dart SAM"] if "Sea Dart SAM" in arming_map else arming_map["weapons"]["Sea Dart SAM"]
    assert isinstance(sea_dart, dict)
    assert sea_dart["armed"] is True
    assert sea_dart["state"] == "Armed"
    assert sea_dart["arming_until"] == 0.0

    resp_repeat = client.post("/weapons/arm", json={"name": "Sea Dart SAM", "state": "Armed"})
    assert resp_repeat.status_code == 200
    assert resp_repeat.json["state"] == "Armed"
    assert not callbacks, "unexpected timer scheduled on repeat arm"

    resp_safe = client.post("/weapons/arm", json={"name": "Sea Dart SAM", "state": "Safe"})
    assert resp_safe.status_code == 200
    assert resp_safe.json["state"] == "Safe"

    arming_map = _load_raw_arming()
    sea_dart = arming_map["Sea Dart SAM"] if "Sea Dart SAM" in arming_map else arming_map["weapons"]["Sea Dart SAM"]
    assert sea_dart["state"] == "Safe"
    assert sea_dart["armed"] is False
    _reset_sim(client)


def test_weapons_fire_requires_lock(monkeypatch: pytest.MonkeyPatch, client) -> None:
    _reset_sim(client)

    callbacks: List[Callable[[], None]] = []

    class TimerStub:
        def __init__(self, interval, func, *, daemon=True):
            callbacks.append(func)

        def start(self):
            return None

    monkeypatch.setattr(weapons_routes.threading, "Timer", lambda interval, func, daemon=True: TimerStub(interval, func))

    runtime = webdash.RUNTIME
    hostile = _spawn_hostile_contact(runtime, name="Mirage III", klass="Aircraft", distance_nm=4.5)
    webdash.clear_primary_contact()
    runtime.radar.priority_id = None

    arm_resp = client.post("/weapons/arm", json={"name": "Sea Dart SAM", "state": "Armed"})
    assert arm_resp.status_code == 200 and arm_resp.json["ok"] is True
    callbacks.pop()()

    no_primary = client.post("/weapons/fire", json={"name": "Sea Dart SAM", "mode": "real"})
    assert no_primary.status_code == 400
    assert no_primary.json["error"] == "NO_PRIMARY"

    lock_resp = client.post("/api/command", json={"cmd": f"/radar lock {hostile.id}"})
    assert lock_resp.status_code == 200
    assert lock_resp.json["ok"] is True
    assert runtime.radar.priority_id == hostile.id

    fire_resp = client.post("/weapons/fire", json={"name": "Sea Dart SAM", "mode": "real"})
    assert fire_resp.status_code == 200
    assert fire_resp.json["ok"] is True
    assert fire_resp.json["result"] == "FIRED"

    _resolve_weapon_events(monkeypatch)
    with webdash.STATE_LOCK:
        webdash.PENDING_EVENTS[:] = [ev for ev in webdash.PENDING_EVENTS if str(ev.get("weapon")) != "Sea Dart SAM"]
    runtime._set_cooldown_until("Sea Dart SAM", time.time() - 1.0)
    webdash.update_weapon_state("Sea Dart SAM", cooldown_until=0.0)

    unlock_resp = client.post("/api/command", json={"cmd": "/radar unlock"})
    assert unlock_resp.status_code == 200
    assert runtime.radar.priority_id is None

    post_unlock = client.post("/weapons/fire", json={"name": "Sea Dart SAM", "mode": "real"})
    assert post_unlock.status_code == 400
    assert post_unlock.json["error"] in {"NO_PRIMARY", "COOLDOWN"}
    _reset_sim(client)
