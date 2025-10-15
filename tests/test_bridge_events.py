from __future__ import annotations

import math
import time

import pytest

import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from projects.falklandV2 import webdash
from projects.falklandV2.radar import Contact, WORLD_N
from projects.falklandV2.routes import weapons as weapons_routes
from projects.falklandV2.subsystems import hermes_cap, webcore


def _spawn_hostile_contact(runtime: Any, *, name: str, klass: str, distance_nm: float) -> Contact:
    """Inject a hostile contact directly into the runtime radar and make it primary."""
    own_x, own_y = runtime._own_xy()
    dist = max(0.05, float(distance_nm))
    x = max(0.0, min(float(WORLD_N), own_x + dist))
    y = own_y
    course = (math.degrees(math.atan2(own_x - x, -(own_y - y))) % 360.0)
    contact = Contact(
        id=runtime.radar._next_id,
        name=name,
        allegiance="Hostile",
        x=float(x),
        y=float(y),
        course_deg=float(course),
        speed_kts=0.0,
        meta={
            "class": klass,
            "cap": {"class": klass},
        },
    )
    runtime.radar._next_id += 1
    runtime.radar.contacts.append(contact)
    try:
        runtime.radar._pending_detection.add(contact.id)
    except Exception:
        pass
    runtime.radar.priority_id = contact.id
    runtime._sync_engine_contacts()
    runtime._update_engine_state_view()
    return contact


def _resolve_weapon_events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Process pending weapon events deterministically (hit every target)."""
    monkeypatch.setattr(webcore.random, "random", lambda: 0.0)
    resolved: list[str] = []
    for event in list(webdash.PENDING_EVENTS):
        kind = str(event.get("kind") or "")
        if kind == "resolve_fire":
            weapon = str(event.get("weapon") or "")
            target_id = event.get("target_id")
            target_name = event.get("target_name")
            target_class = event.get("target_class")
            range_nm = float(event.get("range_nm", 0.0) or 0.0)
            shot_id = str(event.get("shot_id") or "")
            pk = float(event.get("pk", 1.0) or 1.0)
            outcome = webcore._resolve_shot_once(  # type: ignore[attr-defined]
                weapon=weapon,
                target_id=target_id,
                target_name=target_name,
                target_class=target_class,
                range_nm=range_nm,
                shot_id=shot_id,
                pk=pk,
            )
            outcome_event = (
                "weapon.result.hit" if outcome == "hit"
                else "weapon.result.miss" if outcome == "miss"
                else "weapon.result.no_effect"
            )
            webdash.record_event(outcome_event, {
                "weapon": weapon,
                "target_id": target_id,
                "target": target_name,
                "range_nm": range_nm,
                "shooter": "Sheffield",
            })
            webdash.PENDING_EVENTS.remove(event)
            resolved.append(outcome_event)
        elif kind == "weapon_reload_ready":
            weapon_name = str(event.get("weapon") or "")
            webdash.record_event("weapon.reload.complete", {
                "name": weapon_name,
                "source": "reload",
            })
            try:
                webdash.update_weapon_state(weapon_name, cooldown_until=0.0)
            except Exception:
                pass
            webdash.PENDING_EVENTS.remove(event)
    return resolved


@pytest.fixture
def client():
    return webdash.app.test_client()


def _reset_sim(client) -> None:
    resp = client.post("/diag/reset")
    assert resp.status_code == 200
    assert resp.json.get("ok") is True
    with webdash.STATE_LOCK:
        webdash.EVENT_QUEUE.clear()
        webdash.RADIO_QUEUE.clear()
        webdash.RADIO_HISTORY.clear()
        webdash.PENDING_EVENTS.clear()


def test_weapons_routes_generate_events_for_air_and_surface_targets(monkeypatch: pytest.MonkeyPatch, client) -> None:
    _reset_sim(client)

    # Stub the arming timer so completion happens synchronously during the test.
    callbacks: list[Callable[[], None]] = []

    class _TimerStub:
        def __init__(self, interval, func, *, daemon=True):
            callbacks.append(func)

        def start(self):
            return None

    monkeypatch.setattr(weapons_routes.threading, "Timer", lambda interval, func, daemon=True: _TimerStub(interval, func))

    runtime = webdash.RUNTIME

    # Engage an aircraft with Sea Dart.
    aircraft = _spawn_hostile_contact(runtime, name="Mirage III", klass="Aircraft", distance_nm=5.0)
    arm_resp = client.post("/weapons/arm", json={"name": "Sea Dart SAM", "state": "Armed"})
    assert arm_resp.status_code == 200 and arm_resp.json["ok"] is True
    assert callbacks, "arming callback missing"
    callbacks.pop()()

    fire_resp = client.post("/weapons/fire", json={"name": "Sea Dart SAM", "mode": "real"})
    assert fire_resp.status_code == 200
    assert fire_resp.json["ok"] is True
    assert fire_resp.json["result"] == "FIRED"

    # Engage a surface ship with Exocet.
    ship = _spawn_hostile_contact(runtime, name="ARA General Belgrano", klass="Ship", distance_nm=12.0)
    runtime.radar.priority_id = ship.id
    arm_resp = client.post("/weapons/arm", json={"name": "MM38 Exocet", "state": "Armed"})
    assert arm_resp.status_code == 200 and arm_resp.json["ok"] is True
    assert callbacks, "second arming callback missing"
    callbacks.pop()()

    fire_resp = client.post("/weapons/fire", json={"name": "MM38 Exocet", "mode": "real"})
    assert fire_resp.status_code == 200
    assert fire_resp.json["ok"] is True
    assert fire_resp.json["result"] == "FIRED"

    resolved_events = _resolve_weapon_events(monkeypatch)
    assert "weapon.result.hit" in resolved_events

    weapon_fire_ids = [ev["id"] for ev in webdash.EVENT_QUEUE if ev["id"].startswith("weapon.")]
    assert "weapon.fire" in weapon_fire_ids
    assert any(ev["id"] == "weapon.result.hit" and ev["data"].get("target") == "Mirage III" for ev in webdash.EVENT_QUEUE)
    assert any(ev["id"] == "weapon.result.hit" and "Belgrano" in ev["data"].get("target", "") for ev in webdash.EVENT_QUEUE)
    assert all(str(ev.get("text", "")).strip() for ev in webdash.EVENT_QUEUE if ev["id"].startswith("weapon."))

    weapon_radio_events = [entry for entry in webdash.RADIO_QUEUE if str(entry.get("event")).startswith("weapon.")]
    assert weapon_radio_events, "expected radio traffic for weapon engagements"


def test_cap_auto_engage_logs_events_and_radio(monkeypatch: pytest.MonkeyPatch, client) -> None:
    _reset_sim(client)

    runtime = webdash.RUNTIME
    cap = runtime.cap
    if cap is None:
        pytest.skip("Hermes CAP subsystem unavailable")

    target = _spawn_hostile_contact(runtime, name="Super Étendard", klass="Aircraft", distance_nm=3.0)
    cap.bind_target_resolver(lambda cid: target if cid == target.id else None)

    res = cap.request_cap_to_cell(
        target_cell="K10",
        distance_nm=8.0,
        mission_kind="intercept",
        loadout="aim9",
    )
    assert res["ok"] is True
    mission = cap.missions[-1]
    mission.status = "onstation"
    mission.ts["onstation"] = time.time() - 120.0
    mission.ts["etd_rtb"] = time.time() + 600.0
    mission.missiles_left = 2
    cap.set_permission(mission.id, True)

    monkeypatch.setattr(hermes_cap.random, "random", lambda: 0.0)

    result = cap.auto_engage(3.0, target.id, now=time.time())
    assert result is not None
    assert result["hit"] is True

    cap_events = [ev for ev in webdash.EVENT_QUEUE if ev["id"].startswith("cap.weapon.")]
    assert any(ev["id"] == "cap.weapon.fire" for ev in cap_events)
    assert any(ev["id"] == "cap.weapon.hit" for ev in cap_events)
    assert all(str(ev.get("text", "")).strip() for ev in cap_events)

    cap_radio = [entry for entry in webdash.RADIO_QUEUE if str(entry.get("event", "")).startswith("cap.weapon.")]
    assert cap_radio, "expected radio traffic for CAP engagement"


def test_navigation_routes_emit_events_and_record_logs(monkeypatch: pytest.MonkeyPatch, client) -> None:
    _reset_sim(client)

    flight_log: list[dict] = []
    monkeypatch.setattr(webdash, "record_flight", flight_log.append)
    monkeypatch.setattr(webcore, "record_flight", flight_log.append)

    close_resp = client.get("/nav/hermes/close_in")
    assert close_resp.status_code == 200
    assert close_resp.json["ok"] is True

    stand_resp = client.get("/nav/hermes/stand_off")
    assert stand_resp.status_code == 200
    assert stand_resp.json["ok"] is True

    nav_events = [ev for ev in webdash.EVENT_QUEUE if ev["id"].startswith("nav.hermes.")]
    assert any(ev["id"] == "nav.hermes.close_in" for ev in nav_events)
    assert any(ev["id"] == "nav.hermes.stand_off" for ev in nav_events)
    assert all(str(ev.get("text", "")).strip() for ev in nav_events)

    radio_ids = [entry.get("event") for entry in webdash.RADIO_QUEUE]
    assert "nav.hermes.close_in.request" in radio_ids
    assert "nav.hermes.stand_off.request" in radio_ids

    assert any(log.get("route") == "/nav/hermes/close_in" for log in flight_log)
    assert any(log.get("route") == "/nav/hermes/stand_off" for log in flight_log)
